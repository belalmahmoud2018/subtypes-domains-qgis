"""Dialogs for the Subtypes and Domains Manager."""
import traceback

from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import apply as qgis_apply
from . import auto, diag, logic, store
from .compat import origin_expression, plugin_version

FTYPES = [("Integer", "integer"), ("Decimal", "real"), ("Text", "text")]
_KEEP = object()


def _warn(parent, text):
    QMessageBox.warning(parent, "Subtypes and Domains Manager", text)


def _buttons(dialog, ok_text="OK"):
    row = QHBoxLayout()
    row.addStretch(1)
    ok = QPushButton(ok_text)
    cancel = QPushButton("Cancel")
    ok.setDefault(True)
    row.addWidget(ok)
    row.addWidget(cancel)
    ok.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    return row


def _text(table, r, c):
    item = table.item(r, c)
    return item.text().strip() if item else ""


def _num_text(value, ftype):
    """Show a stored number the way the user typed it (1, not 1.0, for integer domains)."""
    if value is None:
        return ""
    if ftype == "integer":
        return str(int(round(float(value))))
    return ("%f" % float(value)).rstrip("0").rstrip(".")


def _parse_number(text, ftype):
    number = float(text)
    if ftype == "integer":
        if number != int(number):
            raise ValueError(text)
        return float(int(number))
    return number


def _check_name(name, what):
    if not name:
        raise ValueError("%s cannot be empty." % what)
    if any(ch in name for ch in "'\"`;\\/"):
        raise ValueError("%s contains a character that is not allowed." % what)
    return name


class ReportDialog(QDialog):
    def __init__(self, title, lines, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(580, 400)
        out = QPlainTextEdit()
        out.setReadOnly(True)
        out.setPlainText("\n".join(lines))
        copy = QPushButton("Copy to clipboard")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(out.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(out)
        lay.addLayout(row)


# ------------------------------------------------------------ domain editor
class DomainEditDialog(QDialog):
    """Create or edit one domain (coded value or range)."""

    def __init__(self, kind, existing_names, domain=None, parent=None):
        super().__init__(parent)
        self.kind, self.existing, self.domain = kind, set(existing_names), domain
        self.result_domain = None
        title = "Coded Value Domain" if kind == "coded" else "Range Domain"
        self.setWindowTitle(("Edit " if domain else "New ") + title)
        self.setMinimumWidth(480)
        form = QFormLayout()
        self.name = QLineEdit(domain["name"] if domain else "")
        self.name.setEnabled(domain is None)
        self.desc = QLineEdit(domain.get("description", "") if domain else "")
        self.ftype = QComboBox()
        for label, key in FTYPES:
            if kind == "range" and key == "text":
                continue
            self.ftype.addItem(label, key)
        if domain:
            self.ftype.setCurrentIndex(max(0, self.ftype.findData(domain["ftype"])))
        form.addRow("Domain name:", self.name)
        form.addRow("Description:", self.desc)
        form.addRow("Field type:", self.ftype)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        if kind == "coded":
            self.table = QTableWidget(0, 2)
            self.table.setHorizontalHeaderLabels(["Code", "Description"])
            self.table.horizontalHeader().setStretchLastSection(True)
            add = QPushButton("Add value")
            rem = QPushButton("Remove selected value")
            add.clicked.connect(lambda: self._add_row())
            rem.clicked.connect(self._remove_row)
            row = QHBoxLayout()
            row.addWidget(add)
            row.addWidget(rem)
            row.addStretch(1)
            lay.addWidget(QLabel("Values:"))
            lay.addWidget(self.table)
            lay.addLayout(row)
            for v in (domain["values"] if domain else []):
                self._add_row(v["code"], v["label"])
        else:
            rform = QFormLayout()
            ftype = domain["ftype"] if domain else "integer"
            self.min_edit = QLineEdit(_num_text(domain.get("min"), ftype) if domain else "")
            self.max_edit = QLineEdit(_num_text(domain.get("max"), ftype) if domain else "")
            rform.addRow("Minimum (empty = no limit):", self.min_edit)
            rform.addRow("Maximum (empty = no limit):", self.max_edit)
            lay.addLayout(rform)
            note = QLabel("Integer ranges with both limits and up to %d values (e.g. 1 to 5) appear in the "
                          "form as a drop-down list of the numbers. Larger or decimal ranges appear as a "
                          "number box, and values outside the range are refused on save." % logic.LIST_LIMIT)
            note.setWordWrap(True)
            lay.addWidget(note)
        lay.addLayout(_buttons(self, "Save"))

    def _add_row(self, code="", label=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(code)))
        self.table.setItem(r, 1, QTableWidgetItem(label))

    def _remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def accept(self):
        try:
            name = _check_name(self.name.text().strip(), "Domain name")
            if self.domain is None and name in self.existing:
                raise ValueError("A domain named '%s' already exists." % name)
            ftype = self.ftype.currentData()
            out = {"name": name, "kind": self.kind, "ftype": ftype,
                   "description": self.desc.text().strip(), "min": None, "max": None, "values": []}
            if self.kind == "coded":
                seen = set()
                for r in range(self.table.rowCount()):
                    code_text, label = _text(self.table, r, 0), _text(self.table, r, 1)
                    if not code_text and not label:
                        continue
                    try:
                        code = store.parse_code(code_text, ftype)
                    except ValueError:
                        raise ValueError("Row %d: '%s' is not a valid %s code." % (r + 1, code_text, ftype))
                    if code in seen:
                        raise ValueError("Row %d: duplicate code %s." % (r + 1, code))
                    seen.add(code)
                    out["values"].append({"code": code, "label": label or str(code)})
                if not out["values"]:
                    raise ValueError("Add at least one value.")
            else:
                for key, edit in (("min", self.min_edit), ("max", self.max_edit)):
                    txt = edit.text().strip()
                    if txt:
                        try:
                            out[key] = _parse_number(txt, ftype)
                        except ValueError:
                            raise ValueError("The %s value '%s' is not a valid %s number." % (
                                "minimum" if key == "min" else "maximum", txt, ftype))
                if out["min"] is not None and out["max"] is not None and out["min"] > out["max"]:
                    raise ValueError("The minimum is greater than the maximum.")
        except ValueError as e:
            _warn(self, str(e))
            return
        self.result_domain = out
        super().accept()


class DomainsDialog(QDialog):
    def __init__(self, fmt, path, model, parent=None):
        super().__init__(parent)
        self.fmt, self.path, self.model = fmt, path, model
        self._names = []
        self.setWindowTitle("Domains - " + fmt.label)
        self.setMinimumSize(520, 380)
        self.list = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Domains stored in the file:"))
        lay.addWidget(self.list)
        for group in (
            (("New Coded Value Domain...", self._new_coded), ("New Range Domain...", self._new_range)),
            (("Edit...", self._edit), ("Delete...", self._delete)),
        ):
            row = QHBoxLayout()
            for text, fn in group:
                b = QPushButton(text)
                b.clicked.connect(fn)
                row.addWidget(b)
            lay.addLayout(row)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        self._names = sorted(self.model["domains"])
        for n in self._names:
            d = self.model["domains"][n]
            if d["kind"] == "coded":
                info = "coded, %s, %d value(s)" % (d["ftype"], len(d["values"]))
            else:
                info = "range, %s, %s to %s" % (
                    d["ftype"], "no minimum" if d["min"] is None else _num_text(d["min"], d["ftype"]),
                    "no maximum" if d["max"] is None else _num_text(d["max"], d["ftype"]))
                if logic.is_listable(d):
                    info += ", shown as a list"
            self.list.addItem("%s    [%s]" % (n, info))

    def _selected(self):
        r = self.list.currentRow()
        return self._names[r] if 0 <= r < len(self._names) else ""

    def _commit(self, mutate):
        try:
            mutate()
            qgis_apply.flush_file(self.path)
            store.save_model(self.fmt, self.path, self.model)
            qgis_apply.flush_file(self.path)
            auto.apply_to_project(self.path)
        except store.DataError as e:
            _warn(self, str(e))
            try:
                fresh = store.load_model(self.fmt, self.path)
            except store.DataError:
                fresh = store.empty_model()
            self.model.clear()
            self.model.update(fresh)
        self._refresh()

    def _new(self, kind):
        dlg = DomainEditDialog(kind, self.model["domains"], None, self)
        if dlg.exec() and dlg.result_domain:
            d = dlg.result_domain
            self._commit(lambda: self.model["domains"].__setitem__(d["name"], d))

    def _new_coded(self):
        self._new("coded")

    def _new_range(self):
        self._new("range")

    def _edit(self):
        name = self._selected()
        if not name:
            _warn(self, "Select a domain first.")
            return
        old = self.model["domains"][name]
        dlg = DomainEditDialog(old["kind"], self.model["domains"], old, self)
        if dlg.exec() and dlg.result_domain:
            d = dlg.result_domain
            self._commit(lambda: self.model["domains"].__setitem__(name, d))

    def _delete(self):
        name = self._selected()
        if not name:
            _warn(self, "Select a domain first.")
            return
        users = sorted({ln for ln, c in self.model["layers"].items()
                        if any(r.get("domain") == name for r in c.get("rules", []))})
        if users:
            _warn(self, "The domain '%s' is used by rules in: %s.\nRemove it from those rules first." % (
                name, ", ".join(users)))
            return
        self._commit(lambda: self.model["domains"].pop(name, None))


# ---------------------------------------------------- subtypes and rules
class SubtypesDialog(QDialog):
    def __init__(self, fmt, path, model, parent=None):
        super().__init__(parent)
        self.fmt, self.path, self.model = fmt, path, model
        qgis_apply.flush_file(path)
        self.layers = {t["name"]: t for t in store.list_layers(fmt, path)}
        self._loading = False
        self.setWindowTitle("Subtypes and Rules - " + fmt.label)
        self.setMinimumSize(760, 640)

        self.layer_combo = QComboBox()
        for n in sorted(self.layers):
            self.layer_combo.addItem(n)
        self.sub_field = QComboBox()
        self.default_combo = QComboBox()
        form = QFormLayout()
        refresh = QPushButton("Refresh fields")
        refresh.setToolTip("Read the layer fields again from the file (after adding a field)")
        refresh.clicked.connect(self._refresh_fields)
        lrow = QHBoxLayout()
        lrow.addWidget(self.layer_combo, 1)
        lrow.addWidget(refresh)
        form.addRow("Layer:", lrow)
        form.addRow("Subtype field (numeric):", self.sub_field)
        form.addRow("Default subtype:", self.default_combo)

        self.sub_table = QTableWidget(0, 2)
        self.sub_table.setHorizontalHeaderLabels(["Subtype code", "Subtype name"])
        self.sub_table.horizontalHeader().setStretchLastSection(True)
        self.sub_table.setMaximumHeight(170)
        add_s = QPushButton("Add subtype")
        rem_s = QPushButton("Remove selected subtype")
        add_s.clicked.connect(lambda: self._add_subtype())
        rem_s.clicked.connect(self._remove_subtype)
        srow = QHBoxLayout()
        srow.addWidget(add_s)
        srow.addWidget(rem_s)
        srow.addStretch(1)

        self.rule_table = QTableWidget(0, 4)
        self.rule_table.setHorizontalHeaderLabels(["Field", "Subtype", "Domain", "Default value"])
        self.rule_table.horizontalHeader().setStretchLastSection(True)
        add_r = QPushButton("Add rule")
        rem_r = QPushButton("Remove selected rule")
        add_r.clicked.connect(lambda: self._add_rule())
        rem_r.clicked.connect(self._remove_rule)
        rrow = QHBoxLayout()
        rrow.addWidget(add_r)
        rrow.addWidget(rem_r)
        rrow.addStretch(1)

        save = QPushButton("Save")
        clear = QPushButton("Remove this layer's setup")
        close = QPushButton("Close")
        save.clicked.connect(self._save)
        clear.clicked.connect(self._clear)
        close.clicked.connect(self.accept)
        brow = QHBoxLayout()
        brow.addWidget(clear)
        brow.addStretch(1)
        brow.addWidget(save)
        brow.addWidget(close)

        self.types_label = QLabel("")
        self.types_label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.types_label)
        lay.addWidget(QLabel("Subtypes:"))
        lay.addWidget(self.sub_table)
        lay.addLayout(srow)
        hint = QLabel("Rules (one row per field and subtype): Field = the field whose values depend on the "
                      "subtype (not the subtype field itself), Subtype = which subtype, Domain = the list "
                      "allowed for that subtype, Default value = optional.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addWidget(self.rule_table)
        lay.addLayout(rrow)
        lay.addLayout(brow)

        self.layer_combo.currentIndexChanged.connect(self._load_layer)
        self.sub_field.currentIndexChanged.connect(self._subtype_field_changed)
        self.sub_table.itemChanged.connect(self._subtypes_changed)
        self._load_layer()

    # -- helpers
    def _layer(self):
        return self.layers.get(self.layer_combo.currentText())

    def _rule_fields(self):
        sf = self.sub_field.currentData()
        return [n for n in self._field_names() if n != sf]

    def _fill_field_combo(self, combo, selected):
        combo.blockSignals(True)
        combo.clear()
        for n in self._rule_fields():
            combo.addItem(n)
        i = combo.findText(selected) if selected else -1
        if selected and i < 0:
            combo.addItem("%s (not found)" % selected)
            i = combo.count() - 1
        combo.setCurrentIndex(max(i, 0))
        combo.blockSignals(False)

    def _rebuild_rule_fields(self):
        for r in range(self.rule_table.rowCount()):
            combo = self.rule_table.cellWidget(r, 0)
            current = combo.currentText().replace(" (not found)", "")
            self._fill_field_combo(combo, current)

    def _subtype_field_changed(self, _index=None):
        if not self._loading:
            self._rebuild_rule_fields()

    def _field_status(self):
        """Why fields visible in QGIS are missing from the file.

        Returns (missing_fields, reason) where reason is one of
        'virtual', 'modified', 'editing', 'unknown' or '' when nothing is missing.
        """
        layer = qgis_apply.find_loaded(self.path, self.layer_combo.currentText())
        if layer is None:
            return [], ""
        in_file = {n.lower() for n in self._field_names()}
        fields = layer.fields()
        pk = set()
        if hasattr(layer, "primaryKeyAttributes"):
            pk = {fields.at(i).name() for i in layer.primaryKeyAttributes()}
        missing, virtual = [], []
        for i in range(fields.count()):
            name = fields.at(i).name()
            if name.lower() in in_file or name in pk or name.lower() in ("fid", "objectid", "ogc_fid"):
                continue
            missing.append(name)
            if fields.fieldOrigin(i) == origin_expression():
                virtual.append(name)
        if not missing:
            return [], ""
        if virtual:
            return virtual, "virtual"
        if layer.isModified():
            return missing, "modified"
        if layer.isEditable():
            return missing, "editing"
        return missing, "unknown"

    def _unsaved_fields(self):
        return self._field_status()[0]

    def _field_advice(self):
        names, reason = self._field_status()
        if not names:
            return ""
        joined = ", ".join(names)
        if reason == "virtual":
            return ("%s is a VIRTUAL field: it exists only inside QGIS, never in the file. Delete it "
                    "(Layer Properties > Fields) and create a real field instead: Field Calculator with "
                    "'Create virtual field' UNticked, or the 'New field' button in edit mode." % joined)
        if reason == "modified":
            return ("%s is not saved yet. Click 'Save Layer Edits' (or turn editing off and choose Save), "
                    "then press 'Refresh fields'." % joined)
        if reason == "editing":
            return ("%s was saved, but the layer is still in edit mode. Turn editing off (the pencil), "
                    "then press 'Refresh fields'." % joined)
        return ("%s exists in QGIS but the file does not show it yet. Remove the layer from the project "
                "and add it again, then press 'Refresh fields'." % joined)

    def _refresh_fields(self):
        qgis_apply.flush_file(self.path)
        try:
            self.layers = {t["name"]: t for t in store.list_layers(self.fmt, self.path)}
        except store.DataError as e:
            _warn(self, str(e))
            return
        keep = self.sub_field.currentData()
        self._loading = True
        self._fill_subtype_field_combo(keep)
        self._loading = False
        self._rebuild_rule_fields()
        self._update_types_label()

    def _fill_subtype_field_combo(self, selected):
        self.sub_field.clear()
        self.sub_field.addItem("(none)", None)
        layer = self._layer() or {"fields": [], "raw": []}
        for fname, kind in layer["fields"]:
            if kind == "integer":
                self.sub_field.addItem("%s (integer)" % fname, fname)
        for fname, kind in layer["fields"]:
            if kind == "real":
                self.sub_field.addItem("%s (decimal)" % fname, fname)
        idx = self.sub_field.findData(selected)
        self.sub_field.setCurrentIndex(idx if idx >= 0 else 0)

    def _update_types_label(self):
        layer = self._layer() or {"fields": [], "raw": []}
        text = "Field types read from the file: " + (
            ", ".join("%s: %s" % (n, t) for n, t in layer["raw"]) or "none")
        advice = self._field_advice()
        if advice:
            text += "\n\u26a0 " + advice
        self.types_label.setText(text)

    def _field_names(self):
        t = self._layer()
        return [n for n, _k in t["fields"]] if t else []

    def _kinds(self):
        t = self._layer()
        return dict(t["fields"]) if t else {}

    def _read_subtypes(self):
        out = []
        for r in range(self.sub_table.rowCount()):
            code_text, name = _text(self.sub_table, r, 0), _text(self.sub_table, r, 1)
            if not code_text and not name:
                continue
            try:
                code = int(code_text)
            except ValueError:
                continue
            out.append({"code": code, "name": name or str(code)})
        return out

    def _fill_subtype_combo(self, combo, selected):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All subtypes", None)
        for s in self._read_subtypes():
            combo.addItem("%d - %s" % (s["code"], s["name"]), s["code"])
        idx = combo.findData(selected)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _rebuild_default_combo(self, selected=_KEEP):
        current = self.default_combo.currentData() if selected is _KEEP else selected
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("(none)", None)
        for s in self._read_subtypes():
            self.default_combo.addItem("%d - %s" % (s["code"], s["name"]), s["code"])
        idx = self.default_combo.findData(current)
        self.default_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.default_combo.blockSignals(False)

    def _subtypes_changed(self, _item=None):
        if self._loading:
            return
        self._rebuild_default_combo()
        for r in range(self.rule_table.rowCount()):
            combo = self.rule_table.cellWidget(r, 1)
            self._fill_subtype_combo(combo, combo.currentData())

    # -- loading a layer's stored setup
    def _load_layer(self):
        self._loading = True
        name = self.layer_combo.currentText()
        cfg = self.model["layers"].get(name, {})
        self._fill_subtype_field_combo(cfg.get("subtype_field"))
        self._update_types_label()
        self.sub_table.setRowCount(0)
        for s in cfg.get("subtypes", []):
            self._add_subtype(s["code"], s["name"])
        self._rebuild_default_combo(cfg.get("default_subtype"))
        self.rule_table.setRowCount(0)
        for r in cfg.get("rules", []):
            self._add_rule(r)
        self._loading = False

    def _add_subtype(self, code="", name=""):
        was = self._loading
        self._loading = True
        r = self.sub_table.rowCount()
        self.sub_table.insertRow(r)
        self.sub_table.setItem(r, 0, QTableWidgetItem(str(code)))
        self.sub_table.setItem(r, 1, QTableWidgetItem(name))
        self._loading = was
        if not was:
            self._subtypes_changed()

    def _remove_subtype(self):
        r = self.sub_table.currentRow()
        if r >= 0:
            self.sub_table.removeRow(r)
            self._subtypes_changed()

    def _add_rule(self, rule=None):
        if rule is None and not self._rule_fields():
            advice = self._field_advice() or "If you just added the field, press 'Refresh fields'."
            _warn(self, "This layer has no field to attach a domain to (other than the subtype field).\n\n"
                  + advice)
            return
        r = self.rule_table.rowCount()
        self.rule_table.insertRow(r)
        field = QComboBox()
        self._fill_field_combo(field, rule["field"] if rule else None)
        self.rule_table.setCellWidget(r, 0, field)
        sub = QComboBox()
        self._fill_subtype_combo(sub, rule["subtype"] if rule else None)
        self.rule_table.setCellWidget(r, 1, sub)
        dom = QComboBox()
        dom.addItem("(none)", None)
        for n in sorted(self.model["domains"]):
            dom.addItem(n, n)
        if rule and rule.get("domain"):
            i = dom.findData(rule["domain"])
            if i >= 0:
                dom.setCurrentIndex(i)
        self.rule_table.setCellWidget(r, 2, dom)
        self.rule_table.setItem(r, 3, QTableWidgetItem((rule.get("default") or "") if rule else ""))

    def _remove_rule(self):
        r = self.rule_table.currentRow()
        if r >= 0:
            self.rule_table.removeRow(r)

    # -- saving
    def _collect(self):
        subtypes = self._read_subtypes()
        rules = []
        for r in range(self.rule_table.rowCount()):
            field = self.rule_table.cellWidget(r, 0).currentText()
            domain = self.rule_table.cellWidget(r, 2).currentData()
            default = _text(self.rule_table, r, 3)
            if not domain and not default:
                continue
            rules.append({
                "field": field, "subtype": self.rule_table.cellWidget(r, 1).currentData(),
                "domain": domain, "default": default or None,
            })
        return {
            "subtype_field": self.sub_field.currentData(),
            "default_subtype": self.default_combo.currentData(),
            "subtypes": subtypes, "rules": rules,
        }

    def _save(self):
        name = self.layer_combo.currentText()
        if not name:
            return
        cfg = self._collect()
        issues = logic.rule_issues(cfg, self.model["domains"], self._kinds())
        if issues:
            _warn(self, "Please fix:\n\n- " + "\n- ".join(issues))
            return
        if cfg["subtype_field"] is None and (cfg["subtypes"] or cfg["default_subtype"] is not None):
            _warn(self, "Choose the subtype field, or remove the subtypes.")
            return
        self._store(name, cfg)

    def _clear(self):
        name = self.layer_combo.currentText()
        if name in self.model["layers"]:
            self._store(name, None)
            self._load_layer()

    def _store(self, name, cfg):
        old = self.model["layers"].get(name)
        if cfg is None:
            self.model["layers"].pop(name, None)
        else:
            self.model["layers"][name] = cfg
        try:
            qgis_apply.flush_file(self.path)
            store.save_model(self.fmt, self.path, self.model)
            qgis_apply.flush_file(self.path)
        except store.DataError as e:
            if old is None:
                self.model["layers"].pop(name, None)
            else:
                self.model["layers"][name] = old
            _warn(self, str(e))
            return
        applied = auto.apply_to_project(self.path)
        extra = ("\n\nIt was applied to %d loaded layer(s)." % applied) if applied else (
            "\n\nLoad the layer in QGIS (or press 'Apply to Loaded Layers' in the main window) to activate it.")
        QMessageBox.information(self, "Subtypes and Domains Manager", "Saved." + extra)


# --------------------------------------------------------------- main window
class HubDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.fmt = store.FORMATS[0]
        self.model = store.empty_model()
        self.setWindowTitle("Subtypes and Domains Manager %s" % plugin_version())
        self.setMinimumWidth(580)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>1. Choose the storage format</b>"))
        self.radios = []
        for f in store.FORMATS:
            r = QRadioButton(f.label)
            r.toggled.connect(lambda checked, fm=f: self._format_changed(checked, fm))
            lay.addWidget(r)
            self.radios.append(r)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        lay.addWidget(QLabel("<b>2. Choose the file</b>"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.editingFinished.connect(self._reload)
        open_btn = QPushButton("Open existing...")
        open_btn.clicked.connect(self._open)
        row.addWidget(self.path_edit, 1)
        row.addWidget(open_btn)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        lay.addWidget(QLabel("<b>3. Work with domains and subtypes</b>"))
        for group in (
            (("Domains...", self._domains), ("Subtypes and Rules...", self._subtypes)),
            (("Apply to Loaded Layers", self._apply), ("Validate Data...", self._validate)),
            (("Diagnostics...", self._diagnostics),),
        ):
            arow = QHBoxLayout()
            for text, fn in group:
                b = QPushButton(text)
                b.clicked.connect(fn)
                arow.addWidget(b)
            lay.addLayout(arow)
        self.auto_apply = QCheckBox("Apply the setup automatically whenever layers of a configured file are "
                                    "loaded (recommended)")
        self.auto_apply.setChecked(auto.is_enabled())
        self.auto_apply.toggled.connect(auto.set_enabled)
        lay.addWidget(self.auto_apply)
        self.save_style = QCheckBox("When applying, also save the setup inside the file as its default style "
                                    "(GeoPackage / SpatiaLite)")
        lay.addWidget(self.save_style)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        lay.addWidget(close)
        self.radios[0].setChecked(True)

    def _format_changed(self, checked, fmt):
        if not checked:
            return
        self.fmt = fmt
        self.hint.setText(fmt.hint)
        self.path_edit.clear()
        self.model = store.empty_model()
        self._reload()

    def _open(self):
        start = self.path_edit.text()
        if self.fmt.directory:
            path = QFileDialog.getExistingDirectory(self, "Select the .gdb folder", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", start, self.fmt.open_filter)
        if path:
            self.path_edit.setText(path)
            self._reload()

    def _reload(self):
        path = self.path_edit.text().strip()
        self.model = store.empty_model()
        if not path:
            self.status.setText("")
            return
        if not self.fmt.is_valid(path):
            self.status.setText("File not found or not a valid %s." % self.fmt.label)
            return
        try:
            qgis_apply.flush_file(path)
            self.model = store.load_model(self.fmt, path)
        except store.DataError as e:
            self.status.setText("Could not read the file: %s" % e)
            return
        configured = [n for n, c in self.model["layers"].items() if c.get("rules") or c.get("subtype_field")]
        self.status.setText("%d domain(s), %d layer(s) with subtypes or rules." % (
            len(self.model["domains"]), len(configured)))

    def _need_file(self):
        path = self.path_edit.text().strip()
        if not self.fmt.is_valid(path):
            _warn(self, "Choose an existing %s first." % self.fmt.label)
            return None
        return path

    def _domains(self):
        path = self._need_file()
        if path:
            DomainsDialog(self.fmt, path, self.model, self).exec()
            self._reload()

    def _subtypes(self):
        path = self._need_file()
        if not path:
            return
        try:
            dlg = SubtypesDialog(self.fmt, path, self.model, self)
        except store.DataError as e:
            _warn(self, str(e))
            return
        if not dlg.layers:
            _warn(self, "No layers found in this file.")
            return
        dlg.exec()
        self._reload()

    def _apply(self):
        path = self._need_file()
        if not path:
            return
        try:
            self._reload()
            lines = qgis_apply.apply_all(self.fmt, path, self.model, True, self.save_style.isChecked())
        except store.DataError as e:
            _warn(self, str(e))
            return
        except Exception:
            ReportDialog("Apply failed - please send this text", traceback.format_exc().splitlines(), self).exec()
            return
        lines.append("")
        lines.append("The setup is applied to the layers in the QGIS project. Save the project to keep it, "
                     "or tick the option to store it in the file.")
        ReportDialog("Apply to Loaded Layers", lines, self).exec()

    def _diagnostics(self):
        path = self.path_edit.text().strip()
        if not path:
            _warn(self, "Choose the file first.")
            return
        try:
            lines = diag.run(self.fmt, path)
        except Exception:
            lines = ["Diagnostics failed:"] + traceback.format_exc().splitlines()
        self._reload()
        ReportDialog("Diagnostics - copy this text and send it", lines, self).exec()

    def _validate(self):
        path = self._need_file()
        if not path:
            return
        try:
            self._reload()
            lines, problems = store.validate_data(self.fmt, path, self.model)
        except store.DataError as e:
            _warn(self, str(e))
            return
        except Exception:
            ReportDialog("Validate failed - please send this text", traceback.format_exc().splitlines(), self).exec()
            return
        lines.append("")
        lines.append("Result: all values respect the rules." if not problems
                     else "Result: %d violation(s) found." % problems)
        ReportDialog("Validate Data", lines, self).exec()
