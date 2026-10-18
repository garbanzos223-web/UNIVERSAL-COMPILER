import sys
import tempfile
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.detection import discover_all, scan  # noqa: E402


def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class DetectionTests(unittest.TestCase):
    def test_python(self) -> None:
        root = _project({"requirements.txt": "requests\n", "app.py": "print(1)\n"})
        result = scan(root)
        self.assertIn("python", [c.adapter_id for c in result.candidates])

    def test_node(self) -> None:
        root = _project({"package.json": '{"name":"x"}'})
        self.assertIn("node", [c.adapter_id for c in scan(root).candidates])

    def test_typescript_raises_node(self) -> None:
        root = _project({"package.json": '{"name":"x"}', "tsconfig.json": "{}"})
        ids = [c.adapter_id for c in scan(root).candidates]
        self.assertIn("node", ids)

    def test_cpp_cmake(self) -> None:
        root = _project({"CMakeLists.txt": "project(x)"})
        self.assertIn("cpp-cmake", [c.adapter_id for c in scan(root).candidates])

    def test_rust(self) -> None:
        root = _project({"Cargo.toml": '[package]\nname="x"'})
        self.assertIn("rust-cargo", [c.adapter_id for c in scan(root).candidates])

    def test_go(self) -> None:
        root = _project({"go.mod": "module x\n"})
        self.assertIn("go", [c.adapter_id for c in scan(root).candidates])

    def test_maven(self) -> None:
        root = _project({"pom.xml": "<project/>"})
        self.assertIn("java-maven", [c.adapter_id for c in scan(root).candidates])

    def test_gradle(self) -> None:
        root = _project({"build.gradle": "plugins {}"})
        self.assertIn("java-gradle", [c.adapter_id for c in scan(root).candidates])

    def test_kotlin_gradle(self) -> None:
        root = _project({"build.gradle.kts": "plugins {}"})
        self.assertIn("kotlin-gradle", [c.adapter_id for c in scan(root).candidates])

    def test_dotnet(self) -> None:
        root = _project({"App.csproj": "<Project/>"})
        self.assertIn("dotnet", [c.adapter_id for c in scan(root).candidates])

    def test_php(self) -> None:
        root = _project({"composer.json": "{}"})
        self.assertIn("php", [c.adapter_id for c in scan(root).candidates])

    def test_unity_by_shape(self) -> None:
        root = _project(
            {
                "Assets/scene.unity": "x",
                "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3",
            }
        )
        self.assertIn("unity", [c.adapter_id for c in scan(root).candidates])

    def test_unreal(self) -> None:
        root = _project({"MyGame.uproject": "{}"})
        self.assertIn("unreal", [c.adapter_id for c in scan(root).candidates])

    def test_ignored_directories(self) -> None:
        root = _project({"node_modules/leftover/Cargo.toml": "[package]", "app.py": "x"})
        ids = [c.adapter_id for c in scan(root).candidates]
        self.assertNotIn("rust-cargo", ids)

    def test_ambiguous_monorepo(self) -> None:
        root = _project({"backend/pyproject.toml": "[project]", "frontend/package.json": "{}"})
        result = scan(root)
        self.assertTrue(result.ambiguous)
        self.assertGreaterEqual(len(result.candidates), 2)

    def test_empty_dir(self) -> None:
        root = Path(tempfile.mkdtemp())
        result = scan(root)
        self.assertEqual(result.candidates, [])

    def test_missing_path_raises(self) -> None:
        from ece_autocompiler.errors import DetectionError

        with self.assertRaises(DetectionError):
            scan(Path(tempfile.mkdtemp()) / "does-not-exist")


class ToolchainTests(unittest.TestCase):
    def test_discover_returns_known_tools(self) -> None:
        tools = discover_all()
        self.assertIn("python", tools)
        self.assertIn("node", tools)
        # Python must be found in the environment running these tests.
        self.assertTrue(tools["python"].found)

    def test_to_dict(self) -> None:
        tool = next(iter(discover_all().values()))
        data = tool.to_dict()
        self.assertIn("name", data)
        self.assertIn("found", data)


if __name__ == "__main__":
    unittest.main()
