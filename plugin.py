import os

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from qgis.PyQt.QtGui import QIcon

from .auto import AutoApplyHook
from .dialogs import HubDialog

MENU = "&Subtypes and Domains Manager"
TOOLBAR = "Subtypes and Domains Manager"


class SubtypesDomainsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.toolbar = None
        self.hook = AutoApplyHook()

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Subtypes and Domains Manager...", self.iface.mainWindow())
        self.action.setToolTip(TOOLBAR)
        self.action.triggered.connect(lambda _checked=False: self.run())
        self.iface.addPluginToMenu(MENU, self.action)
        # its own toolbar, visible by default (the shared Plugins toolbar is often hidden)
        self.toolbar = self.iface.addToolBar(TOOLBAR)
        self.toolbar.setObjectName("SubtypesDomainsManagerToolbar")
        self.toolbar.setToolTip(TOOLBAR)
        self.toolbar.addAction(self.action)
        self.toolbar.setVisible(True)
        self.hook.connect()

    def unload(self):
        self.hook.disconnect()
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        self.action = None

    def run(self):
        HubDialog(self.iface, self.iface.mainWindow()).exec()
