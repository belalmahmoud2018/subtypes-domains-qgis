"""Persistence of domains, subtypes and rules inside the data file itself.

The definitions live in five small attribute tables (sd_*) written through GDAL/OGR,
so the same code works for GeoPackage, SpatiaLite and File Geodatabase.
"""
import functools
import os
import time

from osgeo import gdal, ogr

from .logic import KINDS, list_values, to_kind

MIN_GDAL_FGDB = 3060000

TABLES = {
    "sd_domains": [
        ("dom_name", ogr.OFTString), ("dom_kind", ogr.OFTString), ("dom_type", ogr.OFTString),
        ("dom_desc", ogr.OFTString), ("range_min", ogr.OFTReal), ("range_max", ogr.OFTReal),
    ],
    "sd_domain_values": [
        ("dom_name", ogr.OFTString), ("code_int", ogr.OFTInteger), ("code_real", ogr.OFTReal),
        ("code_text", ogr.OFTString), ("label", ogr.OFTString),
    ],
    "sd_layers": [("lyr_name", ogr.OFTString), ("subtype_field", ogr.OFTString)],
    "sd_subtypes": [
        ("lyr_name", ogr.OFTString), ("st_code", ogr.OFTInteger),
        ("st_name", ogr.OFTString), ("is_default", ogr.OFTInteger),
    ],
    "sd_rules": [
        ("lyr_name", ogr.OFTString), ("field_name", ogr.OFTString), ("st_code", ogr.OFTInteger),
        ("dom_name", ogr.OFTString), ("default_val", ogr.OFTString),
    ],
}
LOOKUP_TABLE = "sd_domain_values"

HIDDEN = set(TABLES) | {"feature_datasets", "feature_dataset_members", "layer_styles"}
SPATIALITE_INTERNAL = (
    "sqlite_", "idx_", "spatial_ref_sys", "geometry_columns", "spatialite_", "views_", "virts_",
    "vector_layers", "data_licenses", "sql_statements_log", "elementarygeometries",
    "geom_cols_ref_sys", "raster_coverages", "knn", "iso_metadata", "stored_", "topologies",
    "networks", "se_", "wms_",
)
KIND_BY_OGR = {
    ogr.OFTInteger: "integer", ogr.OFTInteger64: "integer",
    ogr.OFTReal: "real", ogr.OFTString: "text",
}


class DataError(Exception):
    pass


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DataError:
            raise
        except (RuntimeError, OSError) as e:
            msg = str(e)
            if "permission denied" in msg.lower() or "locked" in msg.lower():
                msg += (
                    "\n\nClose edit mode and make sure no other program has the file open "
                    "(layers loaded in QGIS can lock it)."
                )
            raise DataError(msg) from e

    return wrapper


class Fmt:
    def __init__(self, key, label, driver, open_filter, directory=False, hint=""):
        self.key, self.label, self.driver = key, label, driver
        self.open_filter, self.directory, self.hint = open_filter, directory, hint

    def is_valid(self, path):
        if not path:
            return False
        if self.directory:
            return path.lower().endswith(".gdb") and os.path.isdir(path)
        return os.path.isfile(path)

    def uri(self, path, name):
        return "%s|layername=%s" % (path, name)


FORMATS = [
    Fmt("gpkg", "GeoPackage (.gpkg)", "GPKG", "GeoPackage (*.gpkg)",
        hint="Definitions are stored in sd_* tables inside the .gpkg."),
    Fmt("spatialite", "SpatiaLite (.sqlite)", "SQLite",
        "SpatiaLite (*.sqlite *.db *.sqlite3 *.spatialite)",
        hint="Definitions are stored in sd_* tables inside the database."),
    Fmt("filegdb", "File Geodatabase (.gdb)", "OpenFileGDB", "", True,
        hint="Definitions are stored in sd_* tables inside the .gdb (needs GDAL 3.6+, QGIS 3.28+). "
             "They work in QGIS only; ArcGIS does not read them."),
]


def detect_format(path):
    """The storage format of `path` judged by its extension, or None."""
    low = (path or "").lower().rstrip("/\\")
    if low.endswith(".gdb"):
        return FORMATS[2]
    if low.endswith(".gpkg"):
        return FORMATS[0]
    if low.endswith((".sqlite", ".db", ".sqlite3", ".spatialite")):
        return FORMATS[1]
    return None


_CACHE = {}
CACHE_SECONDS = 15


def invalidate(path=None):
    if path is None:
        _CACHE.clear()
    else:
        _CACHE.pop(os.path.normcase(path), None)


def cached_model(fmt, path):
    """(model, {layer: {field: kind}}) for `path`, cached for a few seconds."""
    key = os.path.normcase(path)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]
    model = load_model(fmt, path)
    kinds = {}
    if model["layers"]:
        kinds = {t["name"]: dict(t["fields"]) for t in list_layers(fmt, path)}
    _CACHE[key] = (time.time(), model, kinds)
    return model, kinds


def _hidden(fmt, name):
    low = name.lower()
    if name in HIDDEN or low in HIDDEN:
        return True
    return fmt.key == "spatialite" and low.startswith(SPATIALITE_INTERNAL)


def open_ds(fmt, path, update=False):
    if fmt.directory:
        try:
            version = int(gdal.VersionInfo())
        except Exception:
            version = 0
        if version < MIN_GDAL_FGDB:
            raise DataError("File Geodatabase needs GDAL 3.6 or newer (QGIS 3.28 or newer).")
    flags = [gdal.OF_UPDATE] if update else [gdal.OF_READONLY, gdal.OF_UPDATE]
    last = None
    for flag in flags:
        try:
            ds = gdal.OpenEx(path, gdal.OF_VECTOR | flag, allowed_drivers=[fmt.driver])
        except RuntimeError as e:
            ds, last = None, e
        if ds is not None:
            return ds
        if not fmt.directory:
            break  # the update fallback is only for empty geodatabases
    raise DataError("Could not open %s%s" % (path, (": %s" % last) if last else "."))


# ----------------------------------------------------------------- model
def empty_model():
    return {"domains": {}, "layers": {}}


def _read_rows(ds, name):
    lyr = ds.GetLayerByName(name)
    if lyr is None:
        return []
    defn = lyr.GetLayerDefn()
    names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    lyr.ResetReading()
    rows = []
    for feat in lyr:
        rows.append({n: (feat.GetField(i) if feat.IsFieldSetAndNotNull(i) else None)
                     for i, n in enumerate(names)})
    return rows


@_guard
def load_model(fmt, path):
    ds = open_ds(fmt, path)
    model = empty_model()
    for r in _read_rows(ds, "sd_domains"):
        if r.get("dom_kind") not in ("coded", "range") or r.get("dom_type") not in KINDS:
            continue
        model["domains"][r["dom_name"]] = {
            "name": r["dom_name"], "kind": r["dom_kind"], "ftype": r["dom_type"],
            "description": r.get("dom_desc") or "",
            "min": r.get("range_min"), "max": r.get("range_max"), "values": [],
        }
    key = {"integer": "code_int", "real": "code_real", "text": "code_text"}
    for r in _read_rows(ds, "sd_domain_values"):
        dom = model["domains"].get(r.get("dom_name"))
        if dom is None or dom["kind"] != "coded":
            continue  # rows of range domains are generated, not stored values
        code = r.get(key[dom["ftype"]])
        if code is not None:
            dom["values"].append({"code": code, "label": r.get("label") or str(code)})
    for r in _read_rows(ds, "sd_layers"):
        model["layers"][r["lyr_name"]] = {
            "subtype_field": r.get("subtype_field"), "default_subtype": None,
            "subtypes": [], "rules": [],
        }
    for r in _read_rows(ds, "sd_subtypes"):
        cfg = model["layers"].setdefault(
            r["lyr_name"], {"subtype_field": None, "default_subtype": None, "subtypes": [], "rules": []})
        cfg["subtypes"].append({"code": int(r["st_code"]), "name": r.get("st_name") or ""})
        if r.get("is_default"):
            cfg["default_subtype"] = int(r["st_code"])
    for r in _read_rows(ds, "sd_rules"):
        cfg = model["layers"].setdefault(
            r["lyr_name"], {"subtype_field": None, "default_subtype": None, "subtypes": [], "rules": []})
        cfg["rules"].append({
            "field": r["field_name"],
            "subtype": int(r["st_code"]) if r.get("st_code") is not None else None,
            "domain": r.get("dom_name"), "default": r.get("default_val"),
        })
    ds = None
    return model


def _ensure_table(ds, name):
    lyr = ds.GetLayerByName(name)
    if lyr is None:
        lyr = ds.CreateLayer(name, None, ogr.wkbNone, [])
        if lyr is None:
            raise DataError("Could not create the table %s." % name)
        for fname, ftype in TABLES[name]:
            lyr.CreateField(ogr.FieldDefn(fname, ftype))
    return lyr


def _rewrite(ds, name, rows):
    lyr = _ensure_table(ds, name)
    lyr.ResetReading()
    for fid in [f.GetFID() for f in lyr]:
        lyr.DeleteFeature(fid)
    for row in rows:
        feat = ogr.Feature(lyr.GetLayerDefn())
        for key, value in row.items():
            if value is not None:
                feat.SetField(key, value)
        if lyr.CreateFeature(feat) != 0:
            raise DataError("Could not write a row to %s." % name)


@_guard
def save_model(fmt, path, model):
    invalidate(path)
    ds = open_ds(fmt, path, update=True)
    try:
        dom_rows, val_rows, lyr_rows, st_rows, rule_rows = [], [], [], [], []
        col = {"integer": "code_int", "real": "code_real", "text": "code_text"}
        for dom in model["domains"].values():
            dom_rows.append({
                "dom_name": dom["name"], "dom_kind": dom["kind"], "dom_type": dom["ftype"],
                "dom_desc": dom.get("description") or None,
                "range_min": dom.get("min"), "range_max": dom.get("max"),
            })
            for v in list_values(dom):
                val_rows.append({"dom_name": dom["name"], col[dom["ftype"]]: v["code"], "label": v["label"]})
        for lname, cfg in model["layers"].items():
            lyr_rows.append({"lyr_name": lname, "subtype_field": cfg.get("subtype_field")})
            for s in cfg.get("subtypes", []):
                st_rows.append({
                    "lyr_name": lname, "st_code": s["code"], "st_name": s["name"],
                    "is_default": 1 if cfg.get("default_subtype") == s["code"] else 0,
                })
            for r in cfg.get("rules", []):
                rule_rows.append({
                    "lyr_name": lname, "field_name": r["field"], "st_code": r["subtype"],
                    "dom_name": r.get("domain"), "default_val": r.get("default") or None,
                })
        _rewrite(ds, "sd_domains", dom_rows)
        _rewrite(ds, "sd_domain_values", val_rows)
        _rewrite(ds, "sd_layers", lyr_rows)
        _rewrite(ds, "sd_subtypes", st_rows)
        _rewrite(ds, "sd_rules", rule_rows)
    finally:
        ds = None


# ---------------------------------------------------------------- layers
@_guard
def list_layers(fmt, path):
    """[{'name', 'has_geom', 'fields': [(name, kind)]}] for the user's layers."""
    ds = open_ds(fmt, path)
    out = []
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        name = lyr.GetName()
        if _hidden(fmt, name):
            continue
        defn = lyr.GetLayerDefn()
        fields, raw = [], []
        for j in range(defn.GetFieldCount()):
            fd = defn.GetFieldDefn(j)
            raw.append((fd.GetName(), ogr.GetFieldTypeName(fd.GetType())))
            kind = KIND_BY_OGR.get(fd.GetType())
            if kind:
                fields.append((fd.GetName(), kind))
        out.append({
            "name": name, "has_geom": lyr.GetGeomType() != ogr.wkbNone,
            "fields": fields, "raw": raw,
        })
    ds = None
    return out


@_guard
def validate_data(fmt, path, model):
    """Check every configured layer's data against its rules. Returns (lines, problems)."""
    from .logic import find_violations

    lines, problems = [], 0
    ds = open_ds(fmt, path)
    try:
        configured = [(n, c) for n, c in sorted(model["layers"].items()) if c.get("rules") or c.get("subtype_field")]
        if not configured:
            return ["No layer has subtypes or domain rules yet."], 0
        for lname, cfg in configured:
            lyr = ds.GetLayerByName(lname)
            lines.append("Layer: %s" % lname)
            if lyr is None:
                lines.append("    [X] layer not found in the file")
                problems += 1
                lines.append("")
                continue
            defn = lyr.GetLayerDefn()
            present = {defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())}
            needed = {r["field"] for r in cfg.get("rules", [])} | ({cfg["subtype_field"]} if cfg.get("subtype_field") else set())
            needed &= present

            def features(lyr=lyr, needed=needed):
                lyr.ResetReading()
                for feat in lyr:
                    yield feat.GetFID(), {
                        n: (feat.GetField(n) if feat.IsFieldSetAndNotNull(n) else None) for n in needed
                    }

            found = find_violations(cfg, model["domains"], features())
            if not found:
                lines.append("    [OK] all values respect the rules")
            for msg, info in sorted(found.items()):
                problems += info["count"]
                lines.append("    [X] %s - %d feature(s), e.g. FID %s" % (
                    msg, info["count"], ", ".join(str(f) for f in info["fids"])))
            lines.append("")
    finally:
        ds = None
    return lines, problems


def parse_code(text, kind):
    """Typed domain code from the text a user typed (raises ValueError)."""
    return to_kind(text, kind)
