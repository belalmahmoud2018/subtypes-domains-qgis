Subtypes and Domains Manager (v0.2.8)

Install: QGIS > Plugins > Manage and Install Plugins > Install from ZIP, then restart QGIS.
Open:    Plugins > Subtypes and Domains Manager (or the toolbar icon).

1. Choose the storage format (GeoPackage, SpatiaLite, File Geodatabase) and open the file.
2. Domains...             create coded value or range domains.
                          Integer ranges with both limits and up to 100 values (e.g. 1 to 5)
                          appear as a drop-down list; larger or decimal ranges appear as a number
                          box and out-of-range values are refused on save.
3. Subtypes and Rules...  pick a layer, choose its numeric subtype field, list the subtypes, then add
                          rules: field (not the subtype field) + subtype (or all) + domain and/or
                          default value. Press 'Refresh fields' after adding a field in QGIS.
4. The setup is applied automatically when the layers load (or press Apply to Loaded Layers):
   form drop-downs, constraints and default values. A domain set for one subtype appears only
   for that subtype.
5. Validate Data...       lists existing values that break the domains / subtypes.
6. Diagnostics...         one report to copy when something does not work.

The definitions are stored in five small tables inside the file (sd_domains, sd_domain_values,
sd_layers, sd_subtypes, sd_rules). They work in QGIS only; ArcGIS does not read them.
File Geodatabase needs GDAL 3.6+ (QGIS 3.28+).

License: GNU GPL v2 (see LICENSE). Author: Belal Mahmoud Abdelmonem.
