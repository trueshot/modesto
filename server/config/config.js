const path = require('path');

module.exports = {
  port: process.env.PORT || 3000,
  env: process.env.NODE_ENV || 'development',

  // Paths
  warehousesPath: path.join(__dirname, '../../warehouses'),
  publicPath: path.join(__dirname, '../public'),

  // CORS settings
  corsOptions: {
    origin: '*',
    methods: ['GET', 'POST', 'PUT', 'DELETE'],
    allowedHeaders: ['Content-Type']
  }
};
