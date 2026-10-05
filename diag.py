"""Diagnostics: one report with everything needed to find a problem on the user's machine."""
import sys
import traceback

from osgeo import gdal
from qgis.core import Qgis, QgsProject
from qgis.PyQt.QtCore import QT_VERSION_STR

from . import apply as qgis_apply
from . import auto, logic, store
from .compat import plugin_version


def _tb():
    return ["    " + ln for ln in traceback.format_exc().splitlines()]


def run(fmt, path):
    out = [
        "Plugin version: %s" % plugin_version(),
        "QGIS: %s | GDAL: %s | Python: %s | Qt: %s" % (
            Qgis.version(), gdal.__version__, sys.version.split()[0], QT_VERSION_STR),
        "Format: %s | Path: %s | valid: %s" % (fmt.label, path, fmt.is_valid(path)),
        "Automatic apply: %s" % ("on" if auto.is_enabled() else "off"),
        "",
    ]
    if not fmt.is_valid(path):
        out.append("The path is not a valid %s - nothing else can be checked." % fmt.label)
        return out
    model, kinds = None, {}
    qgis_apply.flush_file(path)
    try:
        store.invalidate(path)
        model = store.load_model(fmt, path)
        out.append("Stored domains: %s" % (", ".join(
            "%s (%s, %s, %d values)" % (d["name"], d["kind"], d["ftype"], len(d["values"]))
            for d in model["domains"].values()) or "none"))
    except Exception:
        out.append("[X] Reading the stored definitions failed:")
        out += _tb()
        return out
    try:
        layers = store.list_layers(fmt, path)
        kinds = {t["name"]: dict(t["fields"]) for t in layers}
        for t in layers:
            out.append("Layer in file: %s | fields: %s" % (t["name"], ", ".join("%s=%s" % x for x in t["raw"])))
    except Exception:
        out.append("[X] Listing the layers failed:")
        out += _tb()
    out.append("")
    for lname, cfg in sorted(model["layers"].items()):
        out.append("Setup for %s: subtype field=%s, subtypes=%s, default=%s" % (
            lname, cfg.get("subtype_field"),
            ", ".join("%s=%s" % (s["code"], s["name"]) for s in cfg.get("subtypes", [])) or "-",
            cfg.get("default_subtype")))
        for r in cfg.get("rules", []):
            out.append("    rule: field=%s subtype=%s domain=%s default=%s" % (
                r["field"], "all" if r["subtype"] is None else r["subtype"], r.get("domain"), r.get("default")))
        issues = logic.rule_issues(cfg, model["domains"], kinds.get(lname, {}))
        for i in issues:
            out.append("    [X] %s" % i)
    if not model["layers"]:
        out.append("No subtype / rule setup stored for any layer yet.")
    out.append("")
    out.append("Layers in the QGIS project:")
    project_layers = list(QgsProject.instance().mapLayers().values())
    if not project_layers:
        out.append("    (none)")
    for lyr in project_layers:
        try:
            lpath, lname = qgis_apply._split_source(lyr.source())
            same = qgis_apply._norm(lpath) == qgis_apply._norm(path)
            out.append("    %s | provider=%s | table=%s | from this file: %s" % (
                lyr.name(), lyr.providerType() if hasattr(lyr, "providerType") else "?", lname, same))
        except Exception:
            out += _tb()
    out.append("")
    out.append("Applying now and reading back what QGIS stored:")
    try:
        lines = qgis_apply.apply_all(fmt, path, model, True, False)
        out += ["    " + ln for ln in "\n".join(lines).splitlines()]
    except Exception:
        out.append("    [X] Apply failed:")
        out += _tb()
    for lname, cfg in sorted(model["layers"].items()):
        layer = qgis_apply.find_loaded(path, lname)
        if layer is None:
            continue
        targets = sorted({r["field"] for r in cfg.get("rules", [])} | (
            {cfg["subtype_field"]} if cfg.get("subtype_field") else set()))
        for fname in targets:
            idx = layer.fields().lookupField(fname)
            if idx < 0:
                out.append("    %s.%s: field not found in the QGIS layer" % (lname, fname))
                continue
            setup = layer.editorWidgetSetup(idx)
            out.append("    %s.%s: widget=%s | constraint=%s | default=%s" % (
                lname, fname, setup.type() or "(default)", layer.constraintExpression(idx) or "-",
                layer.defaultValueDefinition(idx).expression() or "-"))
    lookup = qgis_apply.find_loaded(path, store.LOOKUP_TABLE)
    if model["domains"]:
        out.append("Lookup table %s: %s" % (
            store.LOOKUP_TABLE,
            "loaded, %d row(s)" % lookup.featureCount() if lookup is not None and lookup.isValid() else "NOT loaded"))
    return out
