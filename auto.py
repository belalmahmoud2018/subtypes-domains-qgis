"""Apply the stored setup automatically whenever layers of a configured file are loaded."""
from qgis.core import QgsProject, QgsSettings, QgsVectorLayer
from qgis.PyQt.QtCore import QTimer

from . import apply as qgis_apply
from . import logic, store
from .compat import log, log_exception

SETTING = "SubtypesDomainsManager/auto_apply"


def is_enabled():
    return QgsSettings().value(SETTING, True, type=bool)


def set_enabled(value):
    QgsSettings().setValue(SETTING, bool(value))


def apply_to_loaded(layers, only_path=None):
    """Apply the stored setup to every layer in `layers` that belongs to a configured file.

    Returns the number of layers configured.
    """
    count = 0
    for layer in layers:
        try:
            if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
                continue
            if layer.providerType() not in ("ogr", "spatialite"):
                continue
            path, name = qgis_apply._split_source(layer.source())
            if only_path and qgis_apply._norm(path) != qgis_apply._norm(only_path):
                continue
            fmt = store.detect_format(path)
            if fmt is None or not fmt.is_valid(path) or name in store.TABLES or not name:
                continue
            model, kinds = store.cached_model(fmt, path)
            cfg = model["layers"].get(name)
            if not cfg or not (cfg.get("rules") or cfg.get("subtype_field")):
                continue
            if logic.rule_issues(cfg, model["domains"], kinds.get(name, {})):
                continue
            lookup = None
            if model["domains"]:
                lookup = qgis_apply.load_layer(fmt, path, store.LOOKUP_TABLE, in_legend=False)
            qgis_apply.apply_layer(layer, cfg, model["domains"], kinds.get(name, {}), lookup)
            log("Applied the subtype/domain setup to %s" % layer.name())
            count += 1
        except Exception:
            log_exception("Could not apply the setup to a layer")
    return count


def apply_to_project(path=None):
    return apply_to_loaded(list(QgsProject.instance().mapLayers().values()), only_path=path)


class AutoApplyHook:
    """Connects to the project so the setup follows the layers automatically."""

    def __init__(self):
        self.connected = False

    def connect(self):
        if self.connected:
            return
        QgsProject.instance().layersAdded.connect(self._added)
        QgsProject.instance().readProject.connect(self._read)
        self.connected = True

    def disconnect(self):
        if not self.connected:
            return
        for signal, slot in ((QgsProject.instance().layersAdded, self._added),
                             (QgsProject.instance().readProject, self._read)):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        self.connected = False

    def _added(self, layers):
        if not is_enabled():
            return
        ids = [lyr.id() for lyr in layers]
        QTimer.singleShot(0, lambda: self._apply_ids(ids))

    @staticmethod
    def _apply_ids(ids):
        project = QgsProject.instance()
        apply_to_loaded([project.mapLayer(i) for i in ids if project.mapLayer(i) is not None])

    def _read(self, *_args):
        if not is_enabled():
            return
        QTimer.singleShot(0, apply_to_project)
