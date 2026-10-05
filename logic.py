"""Pure helpers: expression builders and rule matching (no QGIS / GDAL imports).

Data model
----------
domain : {"name", "kind": "coded"|"range", "ftype": "integer"|"real"|"text",
          "description", "min", "max", "values": [{"code", "label"}]}
layer cfg : {"subtype_field": str|None, "default_subtype": int|None,
             "subtypes": [{"code": int, "name": str}],
             "rules": [{"field", "subtype": int|None, "domain": str|None, "default": str|None}]}
A rule with subtype None applies to every subtype ("All").
"""

KINDS = ("integer", "real", "text")
KEY_COLUMN = {"integer": "code_int", "real": "code_real", "text": "code_text"}
LIST_LIMIT = 100  # integer ranges up to this many values are offered as a drop-down list


def is_listable(dom):
    """Coded domains, and small integer ranges (shown as a list of their numbers)."""
    if dom["kind"] == "coded":
        return True
    lo, hi = dom.get("min"), dom.get("max")
    return (dom["ftype"] == "integer" and lo is not None and hi is not None
            and 0 <= int(hi) - int(lo) < LIST_LIMIT)


def list_values(dom):
    if dom["kind"] == "coded":
        return dom["values"]
    if not is_listable(dom):
        return []
    return [{"code": n, "label": str(n)} for n in range(int(dom["min"]), int(dom["max"]) + 1)]


# ----------------------------------------------------------------- quoting
def q_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def q_str(text):
    return "'%s'" % str(text).replace("'", "''")


def q_value(value, kind):
    if kind == "text":
        return q_str(value)
    if kind == "integer":
        return str(int(value))
    return repr(float(value))


def to_kind(text, kind):
    """Parse text typed by the user into a typed value (raises ValueError)."""
    text = str(text).strip()
    if kind == "text":
        return text
    if kind == "integer":
        return int(text)
    return float(text)


# ------------------------------------------------------------ rule matching
def field_rules(cfg, field):
    return [r for r in cfg.get("rules", []) if r["field"] == field]


def rule_for(cfg, field, subtype):
    rules = field_rules(cfg, field)
    for r in rules:
        if r["subtype"] is not None and r["subtype"] == subtype:
            return r
    for r in rules:
        if r["subtype"] is None:
            return r
    return None


def _same(a, b, kind):
    if kind == "text":
        return str(a) == str(b)
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def check_value(dom, value):
    """True when `value` is allowed by the domain (NULL is always allowed)."""
    if value is None or value == "":
        return True
    if dom["kind"] == "coded":
        return any(_same(value, v["code"], dom["ftype"]) for v in dom["values"])
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if dom.get("min") is not None and number < float(dom["min"]):
        return False
    if dom.get("max") is not None and number > float(dom["max"]):
        return False
    return True


# ------------------------------------------------------------- expressions
def domain_condition(field, dom):
    f = q_ident(field)
    if dom["kind"] == "coded":
        vals = [q_value(v["code"], dom["ftype"]) for v in dom["values"]]
        return "%s IN (%s)" % (f, ", ".join(vals)) if vals else "FALSE"
    parts = []
    if dom.get("min") is not None:
        parts.append("%s >= %s" % (f, q_value(dom["min"], dom["ftype"])))
    if dom.get("max") is not None:
        parts.append("%s <= %s" % (f, q_value(dom["max"], dom["ftype"])))
    return " AND ".join(parts) if parts else "TRUE"


def _case(cfg, field, make, fallback, sub_expr=None):
    sf = cfg.get("subtype_field")
    rules = field_rules(cfg, field)
    specific = [r for r in rules if r["subtype"] is not None]
    general = next((r for r in rules if r["subtype"] is None), None)
    else_expr = make(general) if general else fallback
    if not specific or not sf:
        return else_expr
    whens = " ".join(
        "WHEN %s = %d THEN %s" % (sub_expr or q_ident(sf), r["subtype"], make(r)) for r in specific
    )
    return "CASE %s ELSE %s END" % (whens, else_expr)


def build_constraint(cfg, domains, field):
    """Hard constraint expression for `field`, or None when it has no domain."""
    if not any(r.get("domain") in domains for r in field_rules(cfg, field)):
        return None

    def make(rule):
        dom = domains.get(rule.get("domain"))
        return "(%s)" % domain_condition(field, dom) if dom else "TRUE"

    return "%s IS NULL OR (%s)" % (q_ident(field), _case(cfg, field, make, "TRUE"))


def subtype_constraint(cfg):
    sf = cfg.get("subtype_field")
    codes = [str(s["code"]) for s in cfg.get("subtypes", [])]
    if not sf or not codes:
        return None
    return "%s IS NULL OR %s IN (%s)" % (q_ident(sf), q_ident(sf), ", ".join(codes))


def build_default(cfg, field, kind):
    """Default-value expression for `field` (depends on the subtype), or None."""
    if field == cfg.get("subtype_field"):
        code = cfg.get("default_subtype")
        return None if code is None else str(int(code))
    rules = field_rules(cfg, field)
    if not any(r.get("default") not in (None, "") for r in rules):
        return None

    def make(rule):
        raw = rule.get("default")
        if raw in (None, ""):
            return "NULL"
        try:
            return q_value(to_kind(raw, kind), kind)
        except ValueError:
            return "NULL"

    # a field stored before the subtype field is filled in first, so fall back to the default subtype
    sub_expr = None
    if cfg.get("subtype_field") and cfg.get("default_subtype") is not None:
        sub_expr = "coalesce(%s, %d)" % (q_ident(cfg["subtype_field"]), int(cfg["default_subtype"]))
    return _case(cfg, field, make, "NULL", sub_expr)


def widget_plan(cfg, domains, field, kind):
    """Describe the form widget QGIS should use for `field` (or None)."""
    if field == cfg.get("subtype_field"):
        return {"type": "valuemap", "map": [(s["name"], s["code"]) for s in cfg.get("subtypes", [])]}
    rules = [r for r in field_rules(cfg, field) if r.get("domain") in domains]
    if not rules:
        return None
    doms = [domains[r["domain"]] for r in rules]
    specific = [r for r in rules if r["subtype"] is not None]
    if not specific:
        dom = doms[0]
        if is_listable(dom):
            return {"type": "valuemap", "map": [(v["label"], v["code"]) for v in list_values(dom)]}
        return {"type": "range", "min": dom.get("min"), "max": dom.get("max"), "kind": kind}
    sf = cfg.get("subtype_field")
    if sf and not all(is_listable(d) for d in doms) and all(d["kind"] == "range" for d in doms):
        # one spin box for every subtype: widest bounds; the exact range per subtype is
        # enforced by the constraint (QGIS cannot change spin box limits per feature)
        covered = {r["subtype"] for r in rules}
        all_codes = {s["code"] for s in cfg.get("subtypes", [])}
        open_ended = None not in covered and not all_codes <= covered
        mins = [d.get("min") for d in doms]
        maxs = [d.get("max") for d in doms]
        lo = None if open_ended or any(m is None for m in mins) else min(mins)
        hi = None if open_ended or any(m is None for m in maxs) else max(maxs)
        return {"type": "range", "min": lo, "max": hi, "kind": kind}
    if sf and all(is_listable(d) for d in doms):
        general = next((r for r in rules if r["subtype"] is None), None)
        current = "current_value(%s)" % q_str(sf)
        if cfg.get("default_subtype") is not None:
            # a new feature without a chosen subtype behaves like the default subtype
            current = "coalesce(%s, %d)" % (current, int(cfg["default_subtype"]))
        whens = " ".join(
            "WHEN %s = %d THEN \"dom_name\" = %s" % (current, r["subtype"], q_str(r["domain"]))
            for r in specific
        )
        # subtypes without their own rule: the 'All subtypes' domain, otherwise no list at all
        else_expr = '"dom_name" = %s' % q_str(general["domain"]) if general else "FALSE"
        return {
            "type": "valuerelation",
            "key": KEY_COLUMN[kind],
            "value": "label",
            "filter": "CASE %s ELSE %s END" % (whens, else_expr),
        }
    return None


def _num(value, ftype):
    if ftype == "integer":
        return str(int(round(float(value))))
    return ("%f" % float(value)).rstrip("0").rstrip(".")


def domain_summary(dom):
    if dom["kind"] == "coded":
        labels = [str(v["label"]) for v in dom["values"]]
        more = "..." if len(labels) > 6 else ""
        return "one of: %s%s" % (", ".join(labels[:6]), more)
    lo, hi = dom.get("min"), dom.get("max")
    if lo is not None and hi is not None:
        return "from %s to %s" % (_num(lo, dom["ftype"]), _num(hi, dom["ftype"]))
    if lo is not None:
        return "%s or more" % _num(lo, dom["ftype"])
    if hi is not None:
        return "%s or less" % _num(hi, dom["ftype"])
    return "any number"


def constraint_description(cfg, domains, field):
    """Readable rule text QGIS shows when a value is rejected."""
    names = {s["code"]: s["name"] for s in cfg.get("subtypes", [])}
    parts = []
    for r in sorted(field_rules(cfg, field), key=lambda x: (x["subtype"] is not None, x["subtype"] or 0)):
        dom = domains.get(r.get("domain"))
        if dom is None:
            continue
        who = "all subtypes" if r["subtype"] is None else "subtype %s" % names.get(r["subtype"], r["subtype"])
        parts.append("%s: %s" % (who, domain_summary(dom)))
    return "Allowed values - " + "; ".join(parts) if parts else "Value not allowed by the subtype / domain rules"


# --------------------------------------------------------------- validation
def rule_issues(cfg, domains, field_kinds):
    """Problems in a layer configuration (list of readable messages)."""
    issues = []
    sf = cfg.get("subtype_field")
    codes = [s["code"] for s in cfg.get("subtypes", [])]
    if len(set(codes)) != len(codes):
        issues.append("Subtype codes must be unique.")
    if sf and field_kinds.get(sf) not in ("integer", "real"):
        issues.append("The subtype field must be a numeric field (it is: %s)." % field_kinds.get(sf, "not found"))
    if cfg.get("default_subtype") is not None and cfg["default_subtype"] not in codes:
        issues.append("The default subtype is not in the subtype list.")
    seen = set()
    for r in cfg.get("rules", []):
        field = r["field"]
        key = (field, r["subtype"])
        if key in seen:
            issues.append("Field '%s' has two rules for the same subtype." % field)
        seen.add(key)
        if field not in field_kinds:
            issues.append("Field '%s' does not exist in the layer." % field)
            continue
        if sf and field == sf:
            issues.append(
                "Field '%s' is the subtype field itself. Its list comes from the subtypes table; "
                "put the domain on another field (the field whose values depend on the subtype)." % field)
            continue
        if r["subtype"] is not None and r["subtype"] not in codes:
            issues.append("Rule for '%s' uses a subtype that is not defined." % field)
        if r["subtype"] is not None and not sf:
            issues.append("Rules for specific subtypes need a subtype field.")
        dom = domains.get(r.get("domain")) if r.get("domain") else None
        if r.get("domain") and dom is None:
            issues.append("Field '%s': domain '%s' does not exist." % (field, r["domain"]))
        if dom is not None and dom["ftype"] != field_kinds[field]:
            issues.append(
                "Field '%s' is %s but domain '%s' is %s."
                % (field, field_kinds[field], dom["name"], dom["ftype"])
            )
        if r.get("default") not in (None, ""):
            try:
                to_kind(r["default"], field_kinds[field])
            except ValueError:
                issues.append("Field '%s': default value '%s' is not a valid %s."
                              % (field, r["default"], field_kinds[field]))
    return issues


def find_violations(cfg, domains, features, limit=10):
    """Check features against the rules.

    `features` yields (fid, {field: value}). Returns {message: {"count", "fids"}}.
    """
    out = {}

    def record(msg, fid):
        entry = out.setdefault(msg, {"count": 0, "fids": []})
        entry["count"] += 1
        if len(entry["fids"]) < limit:
            entry["fids"].append(fid)

    sf = cfg.get("subtype_field")
    codes = {s["code"] for s in cfg.get("subtypes", [])}
    fields = sorted({r["field"] for r in cfg.get("rules", []) if r.get("domain")})
    for fid, attrs in features:
        sub = None
        if sf:
            raw = attrs.get(sf)
            if raw is not None:
                try:
                    sub = int(raw)
                except (TypeError, ValueError):
                    sub = None
                if codes and sub not in codes:
                    record("%s: undefined subtype value %s" % (sf, raw), fid)
        for fld in fields:
            rule = rule_for(cfg, fld, sub)
            dom = domains.get(rule["domain"]) if rule and rule.get("domain") else None
            if dom is not None and not check_value(dom, attrs.get(fld)):
                record("%s: value not allowed by domain '%s'" % (fld, dom["name"]), fid)
    return out
