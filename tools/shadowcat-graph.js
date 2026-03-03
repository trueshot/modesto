// shadowcat-graph.js
// Tool for ShadowCat's local Neo4j graph (observations, NVRs, cameras, pallets)
//
// Usage:
//   node shadowcat-graph.js query "<cypher>"
//   node shadowcat-graph.js node <type> <name> [prop=value ...]
//   node shadowcat-graph.js import <json-file>
//   node shadowcat-graph.js test
//
// Environment:
//   NEO4J_URI      - default: bolt://localhost:7687
//   NEO4J_USER     - default: neo4j
//   NEO4J_PASSWORD - default: neo4j (change after first run)
//
// Author: neocat gen-0

const neo4j = require('neo4j-driver');

const uri = process.env.NEO4J_URI || 'bolt://localhost:7687';
const username = process.env.NEO4J_USER || 'neo4j';
const password = process.env.NEO4J_PASSWORD || 'neo4j';

const args = process.argv.slice(2);
const command = args[0];

if (!command || command === 'help' || command === '--help') {
  console.log('shadowcat-graph.js - ShadowCat local Neo4j graph');
  console.log('');
  console.log('Usage:');
  console.log('  node shadowcat-graph.js test');
  console.log('  node shadowcat-graph.js query "<cypher>"');
  console.log('  node shadowcat-graph.js node <Type> <name> [prop=value ...]');
  console.log('  node shadowcat-graph.js import <json-file>');
  console.log('');
  console.log('Environment:');
  console.log('  NEO4J_PASSWORD=<password>  (required if not default)');
  console.log('');
  console.log('Connection: ' + uri);
  process.exit(0);
}

const driver = neo4j.driver(uri, neo4j.auth.basic(username, password));

async function testConnection() {
  const session = driver.session();
  try {
    const result = await session.run('RETURN 1 AS test');
    console.log('✓ Connected to Neo4j at ' + uri);
    console.log('  User: ' + username);

    // Get node count
    const countResult = await session.run('MATCH (n) RETURN count(n) AS count');
    const count = countResult.records[0].get('count').toNumber();
    console.log('  Nodes: ' + count);
  } catch (e) {
    console.error('✗ Connection failed: ' + e.message);
    process.exit(1);
  } finally {
    await session.close();
    await driver.close();
  }
}

async function runQuery(cypher) {
  const session = driver.session();
  try {
    const result = await session.run(cypher);
    console.log('Results (' + result.records.length + ' rows):');
    result.records.forEach((record) => {
      const row = {};
      record.keys.forEach((key) => {
        const val = record.get(key);
        if (val && val.properties) {
          row[key] = val.properties;
        } else if (val && val.low !== undefined) {
          row[key] = val.toNumber();
        } else {
          row[key] = val;
        }
      });
      console.log('  ' + JSON.stringify(row));
    });
  } catch (e) {
    console.error('✗ Error: ' + e.message);
    process.exit(1);
  } finally {
    await session.close();
    await driver.close();
  }
}

function parseProps(args) {
  const props = {};
  args.forEach((arg) => {
    const eq = arg.indexOf('=');
    if (eq > 0) {
      const key = arg.substring(0, eq);
      let val = arg.substring(eq + 1);
      if (!isNaN(val)) val = Number(val);
      if (val === 'true') val = true;
      if (val === 'false') val = false;
      props[key] = val;
    }
  });
  return props;
}

async function addNode(type, name, props) {
  const session = driver.session();
  try {
    const setClause = Object.keys(props).map((k) => 'n.' + k + ' = $props.' + k).join(', ');
    let cypher = 'MERGE (n:' + type + ' {name: $name}) ';
    if (setClause) {
      cypher += 'SET ' + setClause + ', n.lastModified = datetime() ';
    } else {
      cypher += 'SET n.lastModified = datetime() ';
    }
    cypher += 'RETURN n';

    await session.run(cypher, { name, props });
    console.log('✓ Node created/updated: (' + type + ' {name: "' + name + '"})');
    if (Object.keys(props).length > 0) {
      console.log('  Properties: ' + JSON.stringify(props));
    }
  } catch (e) {
    console.error('✗ Error: ' + e.message);
    process.exit(1);
  } finally {
    await session.close();
    await driver.close();
  }
}

async function importJson(filePath) {
  const fs = require('fs');
  let data;
  try {
    const content = fs.readFileSync(filePath, 'utf8');
    data = JSON.parse(content);
  } catch (e) {
    console.error('✗ Error reading file: ' + e.message);
    process.exit(1);
  }

  const nodes = data.nodes || [];
  const edges = data.edges || [];
  let nodesCreated = 0;
  let edgesCreated = 0;

  console.log('Importing from ' + filePath);
  console.log('  Nodes: ' + nodes.length);
  console.log('  Edges: ' + edges.length);

  const session = driver.session();
  try {
    for (const node of nodes) {
      const type = node.type;
      const name = node.name;
      if (!type || !name) continue;

      const props = {};
      Object.keys(node).forEach((k) => {
        if (k !== 'type') props[k] = node[k];
      });

      const setClause = Object.keys(props).map((k) => 'n.' + k + ' = $props.' + k).join(', ');
      let cypher = 'MERGE (n:' + type + ' {name: $name}) ';
      if (setClause) {
        cypher += 'SET ' + setClause + ', n.lastModified = datetime() ';
      }
      cypher += 'RETURN n';

      await session.run(cypher, { name, props });
      nodesCreated++;
    }

    for (const edge of edges) {
      const { from, type: rel, to, addedBy } = edge;
      if (!from || !rel || !to) continue;

      const cypher =
        'MATCH (a) WHERE a.name = $from ' +
        'MATCH (b) WHERE b.name = $to ' +
        'MERGE (a)-[r:' + rel + ']->(b) ' +
        'SET r.lastModified = datetime()' +
        (addedBy ? ', r.addedBy = $addedBy ' : ' ') +
        'RETURN r';

      const result = await session.run(cypher, { from, to, addedBy });
      if (result.records.length > 0) edgesCreated++;
    }

    console.log('');
    console.log('✓ Import complete:');
    console.log('  Nodes: ' + nodesCreated + '/' + nodes.length);
    console.log('  Edges: ' + edgesCreated + '/' + edges.length);
  } catch (e) {
    console.error('✗ Error: ' + e.message);
    process.exit(1);
  } finally {
    await session.close();
    await driver.close();
  }
}

// Execute
if (command === 'test') {
  testConnection();
} else if (command === 'query') {
  const cypher = args[1];
  if (!cypher) {
    console.error('Usage: node shadowcat-graph.js query "<cypher>"');
    process.exit(1);
  }
  runQuery(cypher);
} else if (command === 'node') {
  const type = args[1];
  const name = args[2];
  const props = parseProps(args.slice(3));
  if (!type || !name) {
    console.error('Usage: node shadowcat-graph.js node <Type> <name> [prop=value ...]');
    process.exit(1);
  }
  addNode(type, name, props);
} else if (command === 'import') {
  const filePath = args[1];
  if (!filePath) {
    console.error('Usage: node shadowcat-graph.js import <json-file>');
    process.exit(1);
  }
  importJson(filePath);
} else {
  console.error('Unknown command: ' + command);
  process.exit(1);
}
