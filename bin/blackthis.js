#!/usr/bin/env node
"use strict";

const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PKG_ROOT = path.resolve(__dirname, "..");
const PKG_JSON = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"));
const REQUIREMENTS_FILE = path.join(PKG_ROOT, "requirements.txt");
const HOME_STATE_DIR = path.join(os.homedir(), ".blackthis");
const HOME_VENV_DIR = path.join(HOME_STATE_DIR, "venv");
const DEPS_HASH_FILE = path.join(HOME_STATE_DIR, ".deps_hash");
const IS_WIN = process.platform === "win32";

function venvPythonPath(venvDir) {
  return IS_WIN
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
}

function runSync(cmd, args, opts = {}) {
  return spawnSync(cmd, args, {
    stdio: opts.silent ? "pipe" : "inherit",
    encoding: "utf8",
    ...opts,
  });
}

function hasCommand(cmd) {
  const res = runSync(cmd, ["--version"], { silent: true });
  return res.status === 0;
}

function getPythonVersion(pyBin) {
  const res = runSync(
    pyBin,
    ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
    { silent: true }
  );
  if (res.status !== 0 || !res.stdout) return null;
  const [maj, min] = res.stdout.trim().split(".").map(Number);
  if (Number.isNaN(maj) || Number.isNaN(min)) return null;
  return { major: maj, minor: min };
}

function findSuitableSystemPython() {
  const candidates = IS_WIN
    ? ["python", "python3", "py"]
    : ["python3.12", "python3.11", "python3.13", "python3.10", "python3", "python"];

  for (const bin of candidates) {
    const ver = getPythonVersion(bin);
    if (ver && ver.major === 3 && ver.minor >= 10 && ver.minor <= 13) {
      return bin;
    }
  }
  return null;
}

function computeRequirementsHash() {
  const content = fs.readFileSync(REQUIREMENTS_FILE, "utf8");
  return crypto.createHash("sha256").update(content).digest("hex").slice(0, 16);
}

function pythonCanImport(pyBin, moduleName) {
  const res = runSync(pyBin, ["-c", `import ${moduleName}`], { silent: true });
  return res.status === 0;
}

function hasGermanSpacyModel(pyBin) {
  const code = [
    "import spacy",
    "ok = any(spacy.util.is_package(m) for m in ('de_core_news_lg', 'de_core_news_md', 'de_core_news_sm'))",
    "raise SystemExit(0 if ok else 1)",
  ].join("; ");
  const res = runSync(pyBin, ["-c", code], { silent: true });
  return res.status === 0;
}

function ensureEnvironment({ forceSetup = false, skipModel = false } = {}) {
  // 1. Falls direkt im lokalen Projektordner mit fertigem .venv gestartet wird -> direkt wiederverwenden!
  const localVenvPy = venvPythonPath(path.join(PKG_ROOT, ".venv"));
  if (!forceSetup && fs.existsSync(localVenvPy) && pythonCanImport(localVenvPy, "uvicorn")) {
    if (!skipModel && pythonCanImport(localVenvPy, "spacy") && !hasGermanSpacyModel(localVenvPy)) {
      console.log("\nLade lokales deutsches Sprachmodell (de_core_news_lg, ~550 MB, einmalig)...");
      runSync(localVenvPy, ["-m", "spacy", "download", "de_core_news_lg"]);
    }
    return localVenvPy;
  }

  // 2. Persistente Umgebung in ~/.blackthis/venv nutzen (überlebt npx-Updates ohne Modell-Redownload!)
  fs.mkdirSync(HOME_STATE_DIR, { recursive: true });
  const pyBin = venvPythonPath(HOME_VENV_DIR);
  const targetHash = computeRequirementsHash();
  const currentHash = fs.existsSync(DEPS_HASH_FILE)
    ? fs.readFileSync(DEPS_HASH_FILE, "utf8").trim()
    : "";

  const needVenv = forceSetup || !fs.existsSync(pyBin);
  const needDeps = needVenv || currentHash !== targetHash || !pythonCanImport(pyBin, "uvicorn");

  if (needVenv) {
    console.log("==============================================");
    console.log(`  blackthis v${PKG_JSON.version} – Einmalige Einrichtung`);
    console.log("==============================================\n");

    const useUv = hasCommand("uv");
    if (useUv) {
      console.log("1/3 Erstelle lokale Python-3.12-Umgebung via uv (~/.blackthis/venv)...");
      const res = runSync("uv", ["venv", "--seed", "--python", "3.12", HOME_VENV_DIR]);
      if (res.status !== 0) {
        console.error("Fehler beim Erstellen der Python-Umgebung mit uv.");
        process.exit(1);
      }
    } else {
      const sysPy = findSuitableSystemPython();
      if (!sysPy) {
        console.error("\nFehler: Kein passendes Python (3.10 – 3.13) gefunden.");
        console.error("Schnell-Installation auf macOS (1 Befehl im Terminal):");
        console.error("  brew install python@3.12");
        console.error("Oder installiere uv (lädt Python automatisch): https://docs.astral.sh/uv/\n");
        process.exit(1);
      }
      console.log(`1/3 Erstelle lokale Python-Umgebung (${sysPy} -> ~/.blackthis/venv)...`);
      const res = runSync(sysPy, ["-m", "venv", HOME_VENV_DIR]);
      if (res.status !== 0) {
        console.error("Fehler beim Erstellen der virtuellen Python-Umgebung.");
        process.exit(1);
      }
    }
  }

  if (needDeps) {
    console.log("2/3 Installiere Python-Pakete (FastAPI, Uvicorn, spaCy, openpyxl, wordfreq, PyMuPDF)...");
    let installRes;
    if (hasCommand("uv")) {
      installRes = runSync("uv", ["pip", "install", "--python", pyBin, "-r", REQUIREMENTS_FILE]);
    } else {
      runSync(pyBin, ["-m", "pip", "install", "--upgrade", "pip"]);
      installRes = runSync(pyBin, ["-m", "pip", "install", "-r", REQUIREMENTS_FILE]);
    }
    if (installRes.status !== 0) {
      console.error("Fehler bei der Paket-Installation.");
      process.exit(1);
    }
    fs.writeFileSync(DEPS_HASH_FILE, targetHash, "utf8");
  }

  if (!skipModel && !hasGermanSpacyModel(pyBin)) {
    console.log("3/3 Lade lokales deutsches Sprachmodell (de_core_news_lg, ~550 MB, einmalig)...");
    console.log("    Danach läuft blackthis zu 100 % offline ohne Internet.\n");
    const dlRes = runSync(pyBin, ["-m", "spacy", "download", "de_core_news_lg"]);
    if (dlRes.status !== 0) {
      console.warn(
        "\nHinweis: Sprachmodell konnte nicht geladen werden – starte im Regel-Modus.\n" +
          "Du kannst das Modell später mit 'blackthis --setup' nachladen.\n"
      );
    }
  }

  return pyBin;
}

function printHelp() {
  console.log(`
blackthis v${PKG_JSON.version} – 100 % lokaler DSGVO Schwärzer für CSV, Excel & PDF (Offline)

Verwendung:
  blackthis                     Startet lokale Browser-Oberfläche (sucht automatisch freien Port)
  blackthis --port 8799         Bevorzugten Port setzen (Standard: 8765)
  blackthis --no-open           Startet Server ohne den Browser automatisch zu öffnen
  blackthis --cli [OPTIONEN]    Startet den schnellen Terminal-Modus (CSV, Excel & PDF)
  blackthis --setup             Richtet lokale Python-Umgebung & deutsches Sprachmodell ein/neu ein
  blackthis --no-model          Startet ohne Download des 550-MB-Sprachmodells (nur Regeln)
  blackthis --version           Zeigt die Version an
  blackthis --help              Zeigt diese Hilfe an

Beispiel Terminal-Modus:
  blackthis --cli --input meine_datei.csv --vorname Max --nachname Mustermann
  blackthis --cli --input dokument.pdf --vorname Max --nachname Mustermann
`);
}

function main() {
  const args = process.argv.slice(2);
  const cliIdx = args.indexOf("--cli");
  const isCliMode = cliIdx !== -1;

  if (!isCliMode && (args.includes("--help") || args.includes("-h"))) {
    printHelp();
    return;
  }
  if (!isCliMode && (args.includes("--version") || args.includes("-v"))) {
    console.log(`blackthis v${PKG_JSON.version}`);
    return;
  }

  const forceSetup = args.includes("--setup");
  const skipModel = args.includes("--no-model");

  const pyBin = ensureEnvironment({ forceSetup, skipModel });

  if (forceSetup && args.length === 1) {
    console.log("\nEinrichtung abgeschlossen! Starte das Tool jederzeit mit: blackthis\n");
    return;
  }

  const env = {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH
      ? `${PKG_ROOT}${path.delimiter}${process.env.PYTHONPATH}`
      : PKG_ROOT,
  };

  let pyArgs;
  if (isCliMode) {
    const forwarded = args.filter((_, i) => i !== cliIdx && args[i] !== "--setup" && args[i] !== "--no-model");
    pyArgs = ["-m", "dsgvo_redactor.cli", ...forwarded];
  } else {
    const webArgs = [];
    for (let i = 0; i < args.length; i++) {
      const a = args[i];
      if ((a === "--port" || a === "-p") && args[i + 1]) {
        webArgs.push("--port", args[++i]);
      } else if (a === "--host" && args[i + 1]) {
        webArgs.push("--host", args[++i]);
      } else if (a === "--no-open") {
        webArgs.push("--no-open");
      } else if (a === "--allow-network") {
        webArgs.push("--allow-network");
      }
    }
    pyArgs = ["-m", "dsgvo_redactor.web_app", ...webArgs];
  }

  const child = spawn(pyBin, pyArgs, {
    stdio: "inherit",
    env,
  });

  const forwardSignal = (sig) => {
    if (!child.killed) child.kill(sig);
  };
  process.on("SIGINT", () => forwardSignal("SIGINT"));
  process.on("SIGTERM", () => forwardSignal("SIGTERM"));

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });
}

main();
