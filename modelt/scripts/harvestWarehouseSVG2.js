const startTime = Date.now();  // Start time in milliseconds

function logTime(step) {
  console.log(`Elapsed time at ${step}: ${Date.now() - startTime}ms`);
}

const fs = require('fs')
const { parseString } = require('xml2js')

const svgFilename = process.argv[2]

if (!svgFilename) {
   console.error('Please provide an SVG filename as an argument.')
   process.exit(1)
}
function checkForNoneIntersections(obj) {
    const pathToInt = obj.pathToInt;
    const noneIntersections = [];

    for (const [key, value] of Object.entries(pathToInt)) {
        if (value.startsWith('none ')) {
            noneIntersections.push({ [key]: value });
        }
    }

    return noneIntersections;
}
function sortIntersections(pathToInt) {
    return Object.entries(pathToInt)
        .map(([key, value]) => {
            if (value.startsWith('none ')) {
                return { key, value, distance: Infinity };
            }
            const distance = parseInt(value.split('_')[0]);
            return { key, value, distance };
        })
        .sort((a, b) => a.distance - b.distance);
}
function buildGraph(intersections) {
   let graph = {}

   for (const intersection of intersections) {
      const { line1, line2 } = intersection
      if (!graph[line1]) graph[line1] = []
      if (!graph[line2]) graph[line2] = []
      graph[line1].push(line2)
      graph[line2].push(line1)
   }

   return graph
}
function switchOrder(id) {
   let t = id.split('_')
   return t[0] + '_' + t[2] + '_' + t[1]
}
function linesIntersect(x1, y1, x2, y2, x3, y3, x4, y4) {
   const denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
   if (denominator === 0) {
      return false // lines are parallel
   }
   const t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator
   const u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / denominator
   return t > 0 && t < 1 && u > 0 && u < 1
}
function findAllConnections(graph) {
   let visited = new Set()
   let paths = []

   function dfs(node, path) {
      visited.add(node)
      path.push(node)

      for (const neighbor of graph[node] || []) {
         if (!visited.has(neighbor)) {
            dfs(neighbor, [...path])
         }
      }

      paths.push(path)
   }

   for (const node in graph) {
      if (!visited.has(node)) {
         dfs(node, [])
      }
   }

   return paths
}
function getIntersectingCenterlines(start, end, centerlines) {
   debugger
   const intersectingCenterlines = []
   for (const centerlineId in centerlines) {
      const line = centerlines[centerlineId]
      if (
         linesIntersect(
            start.x,
            start.y,
            end.x,
            end.y,
            line.x1,
            line.y1,
            line.x2,
            line.y2,
         )
      ) {
         intersectingCenterlines.push(line.shortName)
      }
   }
   return intersectingCenterlines
}
function make1stGen(node, intersections) {
   const tree = {}
   tree[node.name] = {}

   for (const intersection of intersections) {
      if (
         intersection.includes(node.line1) ||
         intersection.includes(node.line2)
      ) {
         tree[node.name][intersection] = {}
      }
   }

   return tree
}

function make2ndGen(tree, intersections) {
   for (const node in tree) {
      for (const child in tree[node]) {
         for (const intersection of intersections) {
            if (
               intersection.includes(child.split('_')[1]) ||
               intersection.includes(child.split('_')[2])
            ) {
               tree[node][child][intersection] = {}
            }
         }
      }
   }

   return tree
}

function make3rdGen(tree, intersections) {
   for (const node in tree) {
      for (const child in tree[node]) {
         for (const grandchild in tree[node][child]) {
            for (const intersection of intersections) {
               if (
                  intersection.includes(grandchild.split('_')[1]) ||
                  intersection.includes(grandchild.split('_')[2])
               ) {
                  tree[node][child][grandchild][intersection] = {}
               }
            }
         }
      }
   }

   return tree
}
function bfsAllPaths(source, destination, graph) {
   const queue = [[source]]
   const visited = new Set()
   const allPaths = []

   while (queue.length > 0) {
      const path = queue.shift()
      const node = path[path.length - 1]

      if (node === destination) {
         allPaths.push(path)
      }

      if (!visited.has(node)) {
         visited.add(node)
         for (const neighbor of graph[node]) {
            queue.push([...path, neighbor])
         }
      }
   }

   return allPaths
}
function calculatePathLengths(allPaths, intersections) {
   const pathLengths = []
   let shortestLength = 10000
   let shortestPath = ''
   let shortestSegmentLengths
   let shortestNoOfNodes = 100
   for (let path of allPaths) {
      path = path.split(':')
      const segmentLengths = []
      let cumulativeLength = 0
      for (let i = 0; i < path.length - 1; i++) {
         if (path[i] && path[i + 1]) {
            const point1 = intersections[path[i]]
            const point2 = intersections[path[i + 1]]
            const segmentLength = distanceBetweenPoints(point1, point2)
            segmentLengths.push(segmentLength)
            cumulativeLength += segmentLength
         }
      }
      if (shortestLength > parseInt(cumulativeLength)) {
         shortestLength = parseInt(cumulativeLength)
      }
      pathLengths.push({
         path,
         segmentLengths,
         cumulativeLength: parseInt(cumulativeLength),
      })
   }
   let shortList = []
   for (let i = 0; i < pathLengths.length; i++) {
      if (shortestLength === pathLengths[i].cumulativeLength) {
         shortList.push(pathLengths[i])
      }
   }
   let shortNodes = 100
   let theShortNode = {}
   for (let i = 0; i < shortList.length; i++) {
      if (shortList[i].path.length < shortNodes) {
         theShortNode = shortList[i]
         shortNodes = shortList[i].path.length
      }
   }
   return theShortNode
}
function generateJsonOutput(pathLengths) {
   const jsonOutput = []

   for (let i = 0; i < pathLengths.length; i++) {
      const path = pathLengths[i].path
      const segmentLengths = pathLengths[i].segmentLengths
      const cumulativeLengths = []

      let cumLength = 0
      for (let j = 0; j < segmentLengths.length; j++) {
         cumLength += segmentLengths[j]
         cumulativeLengths.push(cumLength)
      }

      const jsonPath = {
         id: `Path ${i + 1}`,
         totLength: pathLengths[i].cumulativeLength,
         path: [],
      }

      for (let j = 0; j < path.length - 1; j++) {
         jsonPath.path.push({ type: 'intersection', id: path[j] })
         jsonPath.path.push({
            type: 'distance',
            length: segmentLengths[j],
            cumLength: cumulativeLengths[j],
         })
      }
      jsonPath.path.push({ type: 'intersection', id: path[path.length - 1] })

      jsonOutput.push(jsonPath)
   }

   return jsonOutput
}
function distanceBetweenPoints(point1, point2) {
   const dx = point2.x - point1.x
   const dy = point2.y - point1.y
   return Math.sqrt(dx * dx + dy * dy)
}

function blendPaths(sourcePaths, destinationPaths) {
   const blendedPaths = new Set()

   sourcePaths.forEach((sourcePath) => {
      destinationPaths.forEach((destinationPath) => {
         const sourceParts = sourcePath.split(':')
         const destinationParts = destinationPath.split(':')
         if (sourceParts[sourceParts.length - 1] === destinationParts[0]) {
            const blendedPath =
               sourcePath + ':' + destinationParts.slice(1).join(':')
            blendedPaths.add(blendedPath)
         }
      })
   })

   return Array.from(blendedPaths)
}
function normalizeIntersection(str) {
   const parts = str.split('_')
   const first = parts[1]
   const second = parts[2]

   if (first.startsWith('D')) {
      return `Int_${first}_${second}`
   } else if (second.startsWith('D')) {
      return `Int_${second}_${first}`
   } else {
      const firstVal = parseInt(first.replace('C', ''))
      const secondVal = parseInt(second.replace('C', ''))
      if (firstVal > secondVal) {
         return `Int_${second}_${first}`
      } else {
         return `Int_${first}_${second}`
      }
   }
}
function normalizeIntersectionString(str) {
   return str.split(':').map(normalizeIntersection).join(':')
}
function findAllPaths(sourcePaths, destinationPaths) {
   const allPaths = []

   for (const sourcePath of sourcePaths) {
      for (const destPath of destinationPaths) {
         const sourceSegments = sourcePath.split(':')
         const destSegments = destPath.split(':')

         // Check if there's an intersection between source and destination paths
         for (let i = 0; i < sourceSegments.length; i++) {
            for (let j = 0; j < destSegments.length; j++) {
               if (sourceSegments[i] === destSegments[j]) {
                  // Found an intersection, combine the paths
                  const combinedPath = [
                     ...sourceSegments.slice(0, i + 1),
                     ...destSegments.slice(j + 1),
                  ]

                  // Check if the combined path has no repeated intersections
                  if (new Set(combinedPath).size === combinedPath.length) {
                     allPaths.push(combinedPath.join(':'))
                  }
               }
            }
         }
      }
   }

   // Remove duplicates
   return [...new Set(allPaths)]
}
//const sourceTree = {...}; // your source tree object
function eliminateNonConnecting(paths) {
   let thePaths = [...paths]
   let newPath = new Set()
   let keepCount = 0
   for (let i = 0; i < thePaths.length; i++) {
      let obj = thePaths[i].split(':')
      let keep = true
      for (let p = 0; p < obj.length - 1; p++) {
         if (obj[p] === obj[p + 1]) {
            keep = false
         }
         let obj1 = obj[p].split('_')
         let obj2 = obj[p + 1].split('_')
         if (
            obj1[1] === obj2[1] ||
            obj1[1] === obj2[2] ||
            obj1[2] === obj2[1] ||
            obj1[2] === obj2[2]
         ) {
         } else {
            keep = false
         }
      }
      if (keep) {
         newPath.add(thePaths[i])
         keepCount++
      } else {
         //console.log('not kept: '+thePaths[i])
      }
   }
   return newPath
}
function pathsToIntersections(
   source,
   destination,
   centerLinesById,
   intersectionsById,
) {
   console.log('source',source)
   console.log('destination',destination)
   //console.log('centerLinesById',centerLinesById)
   //console.log('intersectionsById',intersectionsById)
   if (destination.name !== source.name) {
      let intersectingCenterlines = getIntersectingCenterlines(
         source,
         destination,
         centerLinesById,
      )
      if (source.type === 'intersection') {
         intersectingCenterlines.push(source.name.split('_')[1])
         intersectingCenterlines.push(source.name.split('_')[2])
      }
      if (destination.type === 'intersection') {
         intersectingCenterlines.push(destination.name.split('_')[1])
         intersectingCenterlines.push(destination.name.split('_')[2])
      }
      intersectingCenterlines = [...new Set(intersectingCenterlines)]
      const graph = {}
      for (const intersection in intersectionsById) {
         graph[intersection] = []
         for (const lineSegment of intersectingCenterlines) {
            if (
               intersectionsById[intersection].line1 === lineSegment ||
               intersectionsById[intersection].line2 === lineSegment
            ) {
               for (const otherIntersection in intersectionsById) {
                  if (
                     otherIntersection !== intersection &&
                     (intersectionsById[otherIntersection].line1 ===
                        lineSegment ||
                        intersectionsById[otherIntersection].line2 ===
                           lineSegment)
                  ) {
                     graph[intersection].push(otherIntersection)
                  }
               }
            }
         }
      }
      const allPaths = bfsAllPaths(source.name, destination.name, graph)
      var intersections = new Set()
      var theLineSet = ' ' + intersectingCenterlines.join(' ') + ' '
      for (let x in intersectionsById) {
         if (
            theLineSet.includes(' ' + intersectionsById[x].line1 + ' ') &&
            theLineSet.includes(' ' + intersectionsById[x].line2 + ' ')
         ) {
            //console.log(intersectionsById[x])
            intersections.add(x)
         }
      }
      const sourceTree1stGen = make1stGen(source, intersections)
      const sourceTree2ndGen = make2ndGen(sourceTree1stGen, intersections)
      const sourceTree3rdGen = make3rdGen(sourceTree2ndGen, intersections)
      const destinationTree1stGen = make1stGen(destination, intersections)
      const destinationTree2ndGen = make2ndGen(
         destinationTree1stGen,
         intersections,
      )
      const destinationTree3rdGen = make3rdGen(
         destinationTree2ndGen,
         intersections,
      )
      var sourceTree = sourceTree3rdGen
      var destinationTree = destinationTree3rdGen
      let sourcePaths = []
      for (var first in sourceTree) {
         sourcePaths.push(first)
         for (var second in sourceTree[first]) {
            sourcePaths.push(first + ':' + second)
            for (var third in sourceTree[first][second]) {
               sourcePaths.push(first + ':' + second + ':' + third)
               for (var fourth in sourceTree[first][second][third]) {
                  sourcePaths.push(
                     first + ':' + second + ':' + third + ':' + fourth,
                  )
               }
            }
         }
      }
      let destinationPaths = []
      for (var first in destinationTree) {
         destinationPaths.push(first)
         for (var second in destinationTree[first]) {
            destinationPaths.push(second + ':' + first)
            for (var third in destinationTree[first][second]) {
               destinationPaths.push(third + ':' + second + ':' + first)
               for (var fourth in destinationTree[first][second][third]) {
                  destinationPaths.push(
                     fourth + ':' + third + ':' + second + ':' + first,
                  )
               }
            }
         }
      }
      sourcePaths = sourcePaths.map(normalizeIntersectionString)
      sourcePaths = new Set(sourcePaths)
      sourcePaths = Array.from(sourcePaths)
      destinationPaths = destinationPaths.map(normalizeIntersectionString)
      destinationPaths = new Set(destinationPaths)
      destinationPaths = Array.from(destinationPaths)
      var combined = blendPaths(sourcePaths, destinationPaths)
      let uniquePaths = new Set(combined)

      uniquePaths = eliminateNonConnecting(uniquePaths)
      const pathLengths = calculatePathLengths(uniquePaths, intersectionsById)
      if (Object.keys(pathLengths).length === 0) {
         console.log('got here')
         source.pathToInt[destination.name] = 'none ' + theLineSet
      } else {
         let obj = pathLengths.path
         let ansStr = pathLengths.cumulativeLength + '_'

         for (let q = 0; q < obj.length; q++) {
            let obj1 = obj[q]
            if (q > 0) {
               ansStr += '(' + parseInt(pathLengths.segmentLengths[q - 1]) + ')'
            }
            ansStr += obj1.split('_')[1] + ':' + obj1.split('_')[2]
         }
         ansStr += ')'
         source.pathToInt[destination.name] = ansStr
      }
   }
}
fs.readFile(svgFilename, 'utf8', (err, svgContent) => {
   if (err) {
      console.error('Error reading SVG file:', err)
      process.exit(1)
   }
   console.log('this can take several minutes...')
   console.log(Date.now())
   parseString(svgContent, (err, parsedXml) => {
      if (err) {
         console.error('Error parsing SVG:', err)
         process.exit(1)
      }

      const rects = parsedXml.svg.g[0].rect
      const lines = parsedXml.svg.g[0].line

      const racks = {}
      const centerLines = {}
      const allIntersections = []
      const helperLineList = {}
      rects.forEach((rect) => {
         const id = rect.$.id
         if (id.startsWith('rack_')) {
            const parts = id.split('_')
            const name = parts[1]
            const orientation = parts[2] || 'none'
            const centerLine = parts[3] || ''

            racks[name] = {
               width: parseFloat(rect.$.width),
               height: parseFloat(rect.$.height),
               type: 'rack',
               x: parseFloat(rect.$.x),
               y: parseFloat(rect.$.y),
               orientation: orientation,
               centerLine: centerLine,
            }
         } else if (id.startsWith('Int_')) {
            if (id.split('-').length > 1) {
               allIntersections.push({
                  name: id,
                  type: 'intersection',
                  x: parseFloat(rect.$.x),
                  y: parseFloat(rect.$.y),
                  line1: id.split('_')[1],
                  line2: id.split('_')[2].split('-')[0],
                  helperLine: id.split('_')[2].split('-')[1]
               })
            } else {
               allIntersections.push({
                  name: id,
                  type: 'intersection',
                  x: parseFloat(rect.$.x),
                  y: parseFloat(rect.$.y),
                  line1: id.split('_')[1],
                  line2: id.split('_')[2],
                  helperLine: false
               })
            }
         }
      })
      logTime('flag 1')

      lines.forEach((line) => {
         const id = line.$.id
         if (id.startsWith('centerLine')) {
            const parts = id.split('_')
            const shortName = parts[1]
            const orientation = parts.length > 2 ? parts[2] : 'none'

            centerLines[id] = {
               id: id,
               x1: parseFloat(line.$.x1),
               y1: parseFloat(line.$.y1),
               x2: parseFloat(line.$.x2),
               y2: parseFloat(line.$.y2),
               type: 'centerLine',
               shortName: shortName,
               orientation: orientation,
               intersections: [],
            }
         }
      })
      logTime('flag 1a')
      // Add intersections to centerLines
      allIntersections.forEach((intersection) => {
         const intParts = intersection.name.split('_')
         const line1 = intParts[1]
         const line2 = intParts[2]

         Object.values(centerLines).forEach((centerLine) => {
            if (
               centerLine.shortName === line1 ||
               centerLine.shortName === line2
            ) {
               centerLine.intersections.push({
                  name: intersection.name,
                  type: intersection,
                  line1: intersection.name.split('_')[1],
                  line2: intersection.name.split('_')[2],
                  x: intersection.x,
                  y: intersection.y,
               })
            }
         })
      })
      logTime('flag 2')

      const graph = buildGraph(allIntersections)
      const allConnections = findAllConnections(graph)
      let connectionIndex = {}
      for (var i = 0; i < allConnections.length; i++) {
         connectionIndex[allConnections[i][allConnections[i].length - 1]] = i
      }
      logTime('flag 3')

      // Create centerLinesIndex
      const centerLinesIndex = {}
      const centerLinesById = {}
      Object.values(centerLines).forEach((centerLine, index) => {
         centerLinesIndex[centerLine.shortName] = centerLine.id
         centerLinesById[centerLine.shortName] = JSON.parse(
            JSON.stringify(centerLine),
         )
      })
      logTime('flag 4')
      const intersectionsIndex = {}
      const intersectionsById = {}
      Object.values(allIntersections).forEach((intersection, index) => {
         intersectionsIndex[intersection.name] = index
         intersectionsById[intersection.name] = JSON.parse(
            JSON.stringify(intersection),
         )
         intersectionsById[switchOrder(intersection.name)] = JSON.parse(
            JSON.stringify(intersection),
         )
      })
      logTime('flag 5')
      for (let i in intersectionsById) {
         let source = intersectionsById[i]
         source.pathToInt = {}
         for (let p in intersectionsById) {
            let destination = intersectionsById[p]
            pathsToIntersections(
               source,
               destination,
               centerLinesById,
               intersectionsById,
            )
         }
      }
      logTime('flag 6')
      for (let i in intersectionsById) {
         let obj = intersectionsById[i]
         let noneList = checkForNoneIntersections(obj)
         if (noneList.length > 0) {
            for (let p in noneList) {
               // first get the Intersections ID
               let key = Object.keys(noneList[p])[0];
               // get its closest intersections.
               let itsClosestInts = sortIntersections(intersectionsById[key].pathToInt);
               let shortestPath = ""
               let shortestPathIntersection = ""
               let shortestPathDistance = Infinity
               for (let q in itsClosestInts) {
                  let distanceToThisCloseIntersection = itsClosestInts[q].distance
                  let closeIntersection = intersectionsById[itsClosestInts[q].key]
                  if (closeIntersection.name!==obj.name) {
                     if (obj.name in closeIntersection.pathToInt) {
                        let thePath = closeIntersection.pathToInt[obj.name]
                        if (thePath.startsWith('none ')) {
                        } else {
                           let distance = parseInt(thePath.split('_')[0])
                           let totDist = (distance+distanceToThisCloseIntersection)
                           if (totDist < shortestPathDistance) {
                              shortestPathDistance = totDist
                              shortestPathIntersection = closeIntersection.name
                              shortestPath = thePath
                           }
                        }
                     }
                  }
               }
               let path2 = intersectionsById[shortestPathIntersection].pathToInt[key]
               let path1 = obj.pathToInt[shortestPathIntersection]
               let path1Time = parseInt(path1.split('_')[0])
               let path2Time = parseInt(path2.split('_')[0])
               let totTime = path1Time+path2Time
               if (path1.substr(0,5) === 'none ' || path2.substr(0,5) === 'none ') {
               } else {
                  path1 = path1.split('_')[1]
                  path2 = path2.split('_')[1]
                  path1 = path1.slice(0, -1);
                  path2 = path2.slice(0, -1);
                  path1 = path1.split(')')
                  path1.pop()
                  path1 = path1.join(')')
                  let combined = totTime+'_'+path1+')'+path2
                  obj.pathToInt[key] = combined
               }
            }
         }
      }
      logTime('flag 7')



      for (let i in intersectionsById) {
         let obj = intersectionsById[i]
         let noneList = checkForNoneIntersections(obj)
         if (noneList.length > 0) {
            for (let p in noneList) {
               // first get the Intersections ID
               let key = Object.keys(noneList[p])[0];
               // get its closest intersections.
               let itsClosestInts = sortIntersections(intersectionsById[key].pathToInt);
               let shortestPath = ""
               let shortestPathIntersection = ""
               let shortestPathDistance = Infinity
               for (let q in itsClosestInts) {
                  let distanceToThisCloseIntersection = itsClosestInts[q].distance
                  let closeIntersection = intersectionsById[itsClosestInts[q].key]
                  if (closeIntersection.name!==obj.name) {
                     if (obj.name in closeIntersection.pathToInt) {
                        let thePath = closeIntersection.pathToInt[obj.name]
                        if (thePath.startsWith('none ')) {
                        } else {
                           let distance = parseInt(thePath.split('_')[0])
                           let totDist = (distance+distanceToThisCloseIntersection)
                           if (totDist < shortestPathDistance) {
                              shortestPathDistance = totDist
                              shortestPathIntersection = closeIntersection.name
                              shortestPath = thePath
                           }
                        }
                     }
                  }
               }
               let path2 = intersectionsById[shortestPathIntersection].pathToInt[key]
               let path1 = obj.pathToInt[shortestPathIntersection]
               let path1Time = parseInt(path1.split('_')[0])
               let path2Time = parseInt(path2.split('_')[0])
               let totTime = path1Time+path2Time
               if (path1.substr(0,5) === 'none ' || path2.substr(0,5) === 'none ') {
               } else {
                  path1 = path1.split('_')[1]
                  path2 = path2.split('_')[1]
                  path1 = path1.slice(0, -1);
                  path2 = path2.slice(0, -1);
                  path1 = path1.split(')')
                  path1.pop()
                  path1 = path1.join(')')
                  let combined = totTime+'_'+path1+')'+path2
                  obj.pathToInt[key] = combined
               }
            }
         }
      }

      logTime('flag 5')






      for (let i in intersectionsById) {
         console.log('working on '+i)
         let obj = intersectionsById[i]
         let noneList = checkForNoneIntersections(obj)
         console.log('noneList')
         console.log(noneList)
         if (noneList.length > 0) {
            for (let p in noneList) {
               // first get the Intersections ID
               let key = Object.keys(noneList[p])[0];
               console.log('for Intersection '+i+' it does not have a path to '+key)
               console.log("intersectionsById[i].pathToInt[key]")
               console.log(intersectionsById[i].pathToInt[key])
               console.log("intersectionsById[key].pathToInt[i]")
               console.log(intersectionsById[key].pathToInt[i])
               intersectionsById[i].pathToInt[key] = intersectionsById[key].pathToInt[i]
            //process.exit()
            }
         }
      }

      logTime('flag 9')



















      
      //const sortedIntersections = sortIntersections(intersectionsById["Int_D7_C10"].pathToInt);
      //for (let i in sortedIntersections) {
      //   let obj = sortedIntersections[i]
         // does this have a solution to 
         //console.log(
      //}         
//console.log(sortedIntersections);
//process.exit()
      const outputData = {
         racks: racks,
         //centerLines: centerLines,
         //intersections: allIntersections,
         //allConnections: allConnections,
         //connectionIndex: connectionIndex,
         //centerLinesIndex: centerLinesIndex,
         centerLinesById: centerLinesById,
         //intersectionsIndex: intersectionsIndex,
         intersectionsById: intersectionsById,

         //graph: graph
      }

      fs.writeFile(
         'warehouse_data.json',
         JSON.stringify(outputData, null, 2),
         (err) => {
            if (err) {
               console.error('Error writing JSON file:', err)
            } else {
               console.log('JSON data has been saved to warehouse_data.json')
            }
         },
      )
   })
})
