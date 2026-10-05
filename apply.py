"""Apply the stored subtype / domain setup to QGIS layers (widgets, constraints, defaults)."""
import contextlib
import re

from qgis.core import (
    QgsDefaultValue,
    QgsEditorWidgetSetup,
    QgsProject,
    QgsVectorLayer,
)

from . import logic, store
from .compat import constraint_expression


def _norm(p):
    return (p or "").replace("\\", "/").rstrip("/").lower()


def _split_source(src):
    """(file path, table name) from an OGR or SpatiaLite-provider layer source."""
    if src.startswith("dbname="):
        db = re.search(r"dbname='([^']*)'", src)
        tbl = re.search(r'table="([^"]*)"', src)
        return (db.group(1) if db else ""), (tbl.group(1) if tbl else "")
    parts = src.split("|")
    name = ""
    for p in parts[1:]:
        if p.lower().startswith("layername="):
            name = p.split("=", 1)[1]
    return parts[0], name


def find_loaded(path, layer_name):
    """The project layer that shows `layer_name` of the file `path`, if any."""
    for layer in QgsProject.instance().mapLayers().values():
        src = layer.source() if hasattr(layer, "source") else ""
        lpath, lname = _split_source(src)
        if _norm(lpath) == _norm(path) and lname == layer_name:
            return layer
    return None


def flush_file(path):
    """Make QGIS write what it holds for this file, so GDAL readers/writers see the same data.

    File Geodatabase in particular keeps a field added in QGIS invisible to other readers
    until QGIS reopens its handle. Layers in edit mode are left alone.
    """
    for layer in list(QgsProject.instance().mapLayers().values()):
        with contextlib.suppress(Exception):
            lpath, _name = _split_source(layer.source())
            if _norm(lpath) != _norm(path) or layer.isModified():
                continue  # never touch a layer with unsaved edits
            provider = layer.dataProvider()
            if provider is not None:
                provider.reloadData()


def load_layer(fmt, path, layer_name, in_legend=True):
    layer = find_loaded(path, layer_name)
    if layer is not None:
        if layer_name == store.LOOKUP_TABLE:
            with contextlib.suppress(Exception):
                layer.reload()  # pick up domain values written since it was loaded
        return layer
    layer = QgsVectorLayer(fmt.uri(path, layer_name), layer_name, "ogr")
    if not layer.isValid():
        return None
    QgsProject.instance().addMapLayer(layer, in_legend)
    return layer


def _setup(plan, lookup):
    if plan is None:
        return None
    if plan["type"] == "valuemap":
        return QgsEditorWidgetSetup(
            "ValueMap", {"map": [{str(label): str(code)} for label, code in plan["map"]]}
        )
    if plan["type"] == "range":
        integer = plan["kind"] == "integer"
        lo = plan["min"] if plan["min"] is not None else (-2147483647 if integer else -1e15)
        hi = plan["max"] if plan["max"] is not None else (2147483647 if integer else 1e15)
        return QgsEditorWidgetSetup("Range", {
            "Min": int(lo) if integer else float(lo),
            "Max": int(hi) if integer else float(hi),
            "Step": 1 if integer else 0.1,
            "Style": "SpinBox",
            "AllowNull": True,
            "Precision": 0 if integer else 6,
        })
    if plan["type"] == "valuerelation" and lookup is not None:
        return QgsEditorWidgetSetup("ValueRelation", {
            "Layer": lookup.id(),
            "LayerName": lookup.name(),
            "LayerSource": lookup.publicSource(),
            "LayerProviderName": "ogr",
            "Key": plan["key"],
            "Value": plan["value"],
            "FilterExpression": plan["filter"],
            "AllowNull": True,
            "OrderByValue": False,
            "AllowMulti": False,
            "NofColumns": 1,
            "UseCompleter": False,
            "Description": "",
        })
    return None


PLAN_TEXT = {
    "valuemap": "drop-down list (Value Map)",
    "range": "number spin box (the exact range per subtype is checked when saving)",
    "valuerelation": "drop-down that changes with the subtype (Value Relation)",
}


def _reset_field(layer, idx):
    layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("TextEdit", {}))
    layer.removeFieldConstraint(idx, constraint_expression())
    layer.setConstraintExpression(idx, "")
    layer.setDefaultValueDefinition(idx, QgsDefaultValue())


def apply_layer(layer, cfg, domains, kinds, lookup):
    """Configure `layer` from `cfg`. Returns (configured_fields, missing_fields, notes)."""
    fields = layer.fields()
    sf = cfg.get("subtype_field")
    targets = {r["field"] for r in cfg.get("rules", [])}
    if sf:
        targets.add(sf)
    previous = [f for f in (layer.customProperty("sd/fields") or "").split("|") if f]
    for fname in previous:
        idx = fields.lookupField(fname)
        if fname not in targets and idx >= 0:
            _reset_field(layer, idx)
    done, missing, notes = [], [], []
    for fname in sorted(targets):
        idx = fields.lookupField(fname)
        if idx < 0:
            missing.append(fname)
            continue
        kind = kinds.get(fname, "text")
        plan = logic.widget_plan(cfg, domains, fname, kind)
        setup = _setup(plan, lookup)
        if setup is not None:
            layer.setEditorWidgetSetup(idx, setup)
            notes.append("%s: %s" % (fname, PLAN_TEXT[plan["type"]]))
        elif plan is not None:
            notes.append("%s: the domain list was NOT applied (the lookup table sd_domain_values "
                         "could not be loaded)" % fname)
        else:
            notes.append("%s: no drop-down (range or mixed domains per subtype) - constraint and "
                         "default value only" % fname)
        if fname == sf:
            cexpr = logic.subtype_constraint(cfg)
            cdesc = "Allowed subtypes - " + ", ".join(
                "%s = %s" % (s["code"], s["name"]) for s in cfg.get("subtypes", []))
        else:
            cexpr = logic.build_constraint(cfg, domains, fname)
            cdesc = logic.constraint_description(cfg, domains, fname)
        if cexpr:
            layer.setConstraintExpression(idx, cexpr, cdesc)
            layer.setFieldConstraint(idx, constraint_expression())
        dexpr = logic.build_default(cfg, fname, kind)
        if dexpr:
            layer.setDefaultValueDefinition(idx, QgsDefaultValue(dexpr, False))
        done.append(fname)
    layer.setCustomProperty("sd/fields", "|".join(done))
    return done, missing, notes


def save_style_in_file(layer):
    """Store the layer style (with the form setup) as the file's default style."""
    try:
        res = layer.saveStyleToDatabase(layer.name(), "Subtypes and Domains setup", True, "")
    except Exception as e:
        return str(e)
    if isinstance(res, tuple):
        res = res[0] if res else ""
    return res or ""


def apply_all(fmt, path, model, load_missing=True, save_style=False):
    """Apply every configured layer of `path`. Returns report lines."""
    lines = []
    lookup = None
    if model["domains"]:
        lookup = load_layer(fmt, path, store.LOOKUP_TABLE, in_legend=False)
        if lookup is None:
            lines.append("[X] could not load the table %s from the file (needed for domain lists)"
                         % store.LOOKUP_TABLE)
    kinds_by_layer = {t["name"]: dict(t["fields"]) for t in store.list_layers(fmt, path)}
    for lname, cfg in sorted(model["layers"].items()):
        if not (cfg.get("rules") or cfg.get("subtype_field")):
            continue
        if lname not in kinds_by_layer:
            lines.append("[X] %s - layer not found in the file" % lname)
            continue
        layer = find_loaded(path, lname)
        if layer is None and load_missing:
            layer = load_layer(fmt, path, lname)
        if layer is None:
            lines.append("[X] %s - not loaded in the project" % lname)
            continue
        issues = logic.rule_issues(cfg, model["domains"], kinds_by_layer[lname])
        if issues:
            lines.append("[X] %s - %s" % (lname, "; ".join(issues)))
            continue
        done, missing, notes = apply_layer(layer, cfg, model["domains"], kinds_by_layer[lname], lookup)
        text = "[OK] %s - %d field(s) configured" % (lname, len(done))
        if missing:
            text += " (missing fields: %s)" % ", ".join(missing)
        text += "\n      source: %s" % layer.source()
        for n in notes:
            text += "\n      - %s" % n
        if save_style and fmt.key in ("gpkg", "spatialite"):
            err = save_style_in_file(layer)
            text += " | style saved in the file" if not err else " | could not save the style: %s" % err
        lines.append(text)
    if not lines:
        lines.append("Nothing to apply yet. Define subtypes or domain rules first.")
    return lines
