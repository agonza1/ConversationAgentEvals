// Keep the local capability guard pinned to the selected dev web port.
function wayloDevOrigins(port, configured) {
  return configured !== undefined ? configured : `http://127.0.0.1:${port},http://localhost:${port}`;
}
module.exports = { wayloDevOrigins };
