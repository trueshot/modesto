// zt421.js — Zebra ZT421 industrial printer (box placeholder, real dims)
// Author: pilotbirdcat gen-9
// ModelT viewer conventions (per modeltbabylon gen-16, 2026-08-31):
//   1 world unit = 1 FOOT; left-handed Y-up; front faces +Z at zero rotation;
//   pivot bottom-center (y=0 at floor/surface contact); classic script, no ES modules.
//
// Axis mapping: the ZT421 is 19.5in front-to-back, 13.25in side-to-side,
// 12.75in high. Front (operator side) faces +Z, so the 19.5in run is DEPTH (Z)
// and 13.25in is WIDTH (X).
//
// Usage: var printer = createZT421(scene, 'printer_basil_1');
//        printer.position.set(x, surfaceY, z); printer.rotation.y = dirRadians;

function createZT421(scene, name) {
  var IN = 1 / 12;
  var WIDTH = 13.25 * IN;   // X, side-to-side  (~1.104 ft)
  var DEPTH = 19.5 * IN;    // Z, front-to-back (~1.625 ft)
  var HEIGHT = 12.75 * IN;  // Y               (~1.0625 ft)

  var root = new BABYLON.TransformNode(name, scene);
  root.metadata = { type: 'printer', assetId: name, model: 'ZT421' };

  // Shared material across all ZT421 instances
  var mat = scene.getMaterialByName('zt421Mat');
  if (!mat) {
    mat = new BABYLON.StandardMaterial('zt421Mat', scene);
    mat.diffuseColor = new BABYLON.Color3(0.16, 0.16, 0.18); // zebra charcoal
    mat.specularColor = new BABYLON.Color3(0.05, 0.05, 0.05);
  }

  var body = BABYLON.MeshBuilder.CreateBox(name + '_body', {
    width: WIDTH,
    depth: DEPTH,
    height: HEIGHT
  }, scene);
  body.position.y = HEIGHT / 2; // bottom-center pivot: root y=0 at contact
  body.material = mat;
  body.parent = root;

  return root;
}
