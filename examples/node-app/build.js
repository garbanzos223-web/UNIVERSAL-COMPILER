// Tiny build step used by the example's npm "build" script.
const fs = require("fs");

fs.mkdirSync("dist", { recursive: true });
fs.copyFileSync("src/index.js", "dist/index.js");
console.log("Built dist/index.js");
