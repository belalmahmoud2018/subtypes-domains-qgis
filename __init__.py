# Subtypes and Domains Manager
# Copyright (C) 2026 Belal Mahmoud Abdelmonem
# Licensed under the GNU General Public License v2 or later (see LICENSE).
import sys


def classFactory(iface):
    # After installing a new version over an old one, QGIS can keep the old sub-modules in memory
    # and mix them with the new ones. Drop them so the new code is always used.
    for name in [n for n in sys.modules if n.startswith(__name__ + ".")]:
        del sys.modules[name]
    from .plugin import SubtypesDomainsPlugin
    return SubtypesDomainsPlugin(iface)
