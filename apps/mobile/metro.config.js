// The editor engine lives in packages/shared (outside this app); Metro must watch it to bundle it.
const path = require('node:path');
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);
config.watchFolders = [path.resolve(__dirname, '../../packages/shared')];
module.exports = config;
