// tractor-trailer.js — standard 18-wheeler: day-cab tractor + 53ft dry van
// Author: pilotbirdcat gen-9
// TRUE SCALE IS LAW (George): all dims real-world, in feet. 1 unit = 1 ft.
// Front (cab nose) faces +Z at zero rotation; pivot bottom-center; y=0 at ground.
//
// Real dims used:
//   Trailer: 53 ft long x 8.5 ft (102 in) wide, 13.5 ft overall height, floor at 4 ft
//   Tractor: conventional day cab, ~20 ft, hood to 6.6 ft, cab roof 9.9 ft + fairing
//   Wheels: 3.4 ft dia; steer axle x2, drive tandem x8 (duals), trailer tandem x8 = 18
//   Overall rig: ~70 ft bumper to rear doors
//
// ~600 tris: 10 wheel cylinders (tessellation 12) + 10 boxes.
// Coplanar-face rule: abutting parts overlap 0.1 ft instead of touching exactly.

function createTractorTrailer53(scene, name) {
  function mat(matName, r, g, b, spec) {
    var m = scene.getMaterialByName(matName);
    if (!m) {
      m = new BABYLON.StandardMaterial(matName, scene);
      m.diffuseColor = new BABYLON.Color3(r, g, b);
      var s = (spec == null ? 0.08 : spec);
      m.specularColor = new BABYLON.Color3(s, s, s);
    }
    return m;
  }
  var trailerMat = mat('ttTrailer', 0.88, 0.88, 0.90, 0.25); // white dry van
  var cabMat     = mat('ttCab',     0.55, 0.12, 0.14, 0.3);  // deep red tractor
  var frameMat   = mat('ttFrame',   0.16, 0.16, 0.18);       // chassis/frame
  var tireMat    = mat('ttTire',    0.09, 0.09, 0.10);       // tires

  var root = new BABYLON.TransformNode(name, scene);
  root.metadata = { type: 'vehicle', assetId: name, model: 'tractor-trailer-53' };

  // box helper — dims/positions in feet, y = center height above ground
  function box(part, w, h, d, x, y, z, material) {
    var b = BABYLON.MeshBuilder.CreateBox(name + '_' + part, { width: w, height: h, depth: d }, scene);
    b.position.set(x, y, z);
    b.material = material;
    b.parent = root;
    return b;
  }
  // wheel helper — cylinder lying on the X axis; w = tire width
  function wheel(part, x, z, w) {
    var c = BABYLON.MeshBuilder.CreateCylinder(name + '_' + part, {
      diameter: 3.4, height: w, tessellation: 12
    }, scene);
    c.rotation.z = Math.PI / 2;
    c.position.set(x, 1.7, z);
    c.material = tireMat;
    c.parent = root;
    return c;
  }

  // ---- Trailer (rear of rig; z from -35.5 to +17.5) ----
  box('van',     8.5, 9.5, 53,   0,   8.75, -9,    trailerMat); // floor 4.0 -> top 13.5
  box('rails',   3.0, 0.98, 20,  0,   3.49, -26,   frameMat);   // under-frame at tandems (top 3.98)
  box('landing', 6.0, 2.9, 0.8,  0,   2.53, -1,    frameMat);   // landing gear (top 3.98)
  wheel('tw1L', -3.2, -28,   1.8); wheel('tw1R', 3.2, -28,   1.8); // trailer tandem duals
  wheel('tw2L', -3.2, -32.5, 1.8); wheel('tw2R', 3.2, -32.5, 1.8);

  // ---- Tractor (front of rig; z from +12 to +34.5) ----
  box('chassis', 3.4, 1.2, 22,   0,   3.0,  23,    frameMat);
  box('hood',    6.8, 3.2, 7.1,  0,   5.0,  30.9,  cabMat);     // overlaps cab 0.1
  box('cab',     8.2, 6.5, 7,    0,   6.65, 24,    cabMat);     // y 3.4 -> 9.9
  box('fairing', 7.5, 2.9, 5,    0,   11.25, 23.5, cabMat);     // y 9.8 -> 12.7 (overlaps cab 0.1)
  box('fuelL',   1.2, 1.2, 4,   -4.0, 2.2,  20,    frameMat);
  box('fuelR',   1.2, 1.2, 4,    4.0, 2.2,  20,    frameMat);
  wheel('sw_L', -3.4,  31,   1.0); wheel('sw_R', 3.4,  31,   1.0); // steer axle
  wheel('dw1L', -3.2,  17.5, 1.8); wheel('dw1R', 3.2,  17.5, 1.8); // drive tandem duals
  wheel('dw2L', -3.2,  13.5, 1.8); wheel('dw2R', 3.2,  13.5, 1.8);

  return root;
}

// Self-register with the ModelTAssets registry when present (guarded so the
// file also runs standalone in the codex/test harness).
if (typeof ModelTAssets !== 'undefined') {
  ModelTAssets.register('tractor-trailer', {
    kind: 'vehicle', model: 'tractor-trailer-53', version: 1, create: createTractorTrailer53
  });
}
