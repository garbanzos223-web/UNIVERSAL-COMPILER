# Examples

Small projects used to try the tool by hand. The integration tests create their
own throwaway samples, so these are for demonstration and manual checks.

## `python-hello`

```powershell
$env:PYTHONPATH = "..\..\src"
python -m ece_autocompiler analyze .\python-hello
python -m ece_autocompiler build   .\python-hello -o .\python-hello.ece --overwrite
```

## `node-app`

Requires Node.js. The `build` script copies `src/index.js` to `dist/`, and the
package step contains the build output.

```powershell
$env:PYTHONPATH = "..\..\src"
python -m ece_autocompiler build .\node-app -o .\node-app.ece --consent --overwrite --skip-install
```
