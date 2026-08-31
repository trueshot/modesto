// zt421.js — Zebra ZT421 industrial printer (low-poly, 6 boxes / 72 tris)
// Author: pilotbirdcat gen-9
// Shape from Zebra product photography (front + right-front views, zebra.com).
// ModelT viewer conventions (per modeltbabylon gen-16, 2026-08-31):
//   1 world unit = 1 FOOT; left-handed Y-up; front faces +Z at zero rotation;
//   pivot bottom-center (y=0 at floor/surface contact); classic script, no ES modules.
//
// Real dims: 19.5in front-to-back (Z depth), 13.25in wide (X), 12.75in high (Y).
// Operator faces the 13.25in-wide front panel (+Z side).
//
// Visual decomposition:
//   - light gray sheet-metal body (sides/top/rear)
//   - dark charcoal control-panel column, front operator-left (+X), with teal touchscreen
//   - dark charcoal printhead cover (upper) + media door (lower), front, with a
//     recessed horizontal print-exit slot between them and a silver tear bar inside
//
// Usage: var printer = createZT421(scene, 'printer_basil_1');
//        printer.position.set(x, surfaceY, z); printer.rotation.y = dirRadians;

function createZT421(scene, name) {
  var IN = 1 / 12;

  // Shared materials across all ZT421 instances
  function mat(matName, r, g, b, opts) {
    var m = scene.getMaterialByName(matName);
    if (!m) {
      m = new BABYLON.StandardMaterial(matName, scene);
      m.diffuseColor = new BABYLON.Color3(r, g, b);
      m.specularColor = new BABYLON.Color3(0.08, 0.08, 0.08);
      if (opts && opts.emissive) m.emissiveColor = new BABYLON.Color3(opts.emissive[0], opts.emissive[1], opts.emissive[2]);
      if (opts && opts.specular) m.specularColor = new BABYLON.Color3(opts.specular, opts.specular, opts.specular);
    }
    return m;
  }
  var grayMat   = mat('zt421Gray',   0.70, 0.71, 0.72, { specular: 0.2 });  // sheet metal
  var darkMat   = mat('zt421Dark',   0.13, 0.13, 0.15);                     // charcoal front
  var silverMat = mat('zt421Silver', 0.78, 0.78, 0.80, { specular: 0.4 }); // tear bar
  var screenMat = mat('zt421Screen', 0.10, 0.35, 0.50, { emissive: [0.15, 0.55, 0.75] }); // touchscreen

  var root = new BABYLON.TransformNode(name, scene);
  root.metadata = { type: 'printer', assetId: name, model: 'ZT421' };

  // helper: box with dims/position in INCHES, y = center height from floor
  function box(part, w, h, d, x, yCenter, z, material) {
    var b = BABYLON.MeshBuilder.CreateBox(name + '_' + part, {
      width: w * IN, height: h * IN, depth: d * IN
    }, scene);
    b.position.x = x * IN;
    b.position.y = yCenter * IN;
    b.position.z = z * IN;
    b.material = material;
    b.parent = root;
    return b;
  }

  // Main sheet-metal body — sides, top, rear (front face recessed 1.2in)
  box('body', 13.25, 12.75, 18.3, 0, 6.375, -0.6, grayMat);

  // Control-panel column — front operator-left (+X), slightly proud of body
  box('panel', 4.6, 12.75, 1.5, 4.325, 6.375, 8.99, darkMat);

  // Touchscreen on the panel — emissive teal, proud of panel face
  box('screen', 2.2, 3.6, 0.25, 4.325, 8.6, 9.85, screenMat);

  // Printhead cover — upper front media section
  box('printhead', 8.65, 5.8, 1.5, -2.3, 9.85, 8.99, darkMat);

  // Media door — lower front media section (gap above = print-exit slot,
  // recessed gray body face shows through the 1.55in slot)
  box('mediadoor', 8.65, 5.4, 1.5, -2.3, 2.7, 8.99, darkMat);

  // Tear bar — silver strip inside the slot
  box('tearbar', 8.3, 0.5, 0.7, -2.3, 6.65, 9.0, silverMat);

  return root;
}
