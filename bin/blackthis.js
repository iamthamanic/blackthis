#!/usr/bin/env node
"use strict";

const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");
const readline = require("readline");

const PKG_ROOT = path.resolve(__dirname, "..");
const PKG_JSON = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"));
const REQUIREMENTS_FILE = path.join(PKG_ROOT, "requirements.txt");
const HOME_STATE_DIR = path.join(os.homedir(), ".blackthis");
const HOME_VENV_DIR = path.join(HOME_STATE_DIR, "venv");
const DEPS_HASH_FILE = path.join(HOME_STATE_DIR, ".deps_hash");
const IS_WIN = process.platform === "win32";
const NPM_BIN = IS_WIN ? "npm.cmd" : "npm";
const NPX_BIN = IS_WIN ? "npx.cmd" : "npx";
const UPDATE_CHECK_TIMEOUT_MS = 2000;

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

function parseSemver(ver) {
  if (!ver || typeof ver !== "string") return null;
  const clean = ver.trim().replace(/^v/i, "").split("-", 1)[0];
  const parts = clean.split(".").map((n) => Number.parseInt(n, 10));
  if (parts.length < 2 || parts.some(Number.isNaN)) return null;
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0];
}

function isNewerVersion(latest, current) {
  const a = parseSemver(latest);
  const b = parseSemver(current);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

function fetchLatestNpmVersion(pkgName, timeoutMs = UPDATE_CHECK_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (val) => {
      if (!settled) {
        settled = true;
        resolve(val);
      }
    };

    const req = https.get(
      `https://registry.npmjs.org/${encodeURIComponent(pkgName)}/latest`,
      {
        headers: {
          Accept: "application/json",
          "User-Agent": `${pkgName}/${PKG_JSON.version}`,
        },
        timeout: timeoutMs,
      },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          return done(null);
        }
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          body += chunk;
          if (body.length > 262144) {
            req.destroy();
            done(null);
          }
        });
        res.on("end", () => {
          try {
            const parsed = JSON.parse(body);
            done(typeof parsed.version === "string" ? parsed.version : null);
          } catch {
            done(null);
          }
        });
      }
    );

    req.on("timeout", () => {
      req.destroy();
      done(null);
    });
    req.on("error", () => done(null));
  });
}

function askYesNo(question, defaultYes = true) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(question, (answer) => {
      rl.close();
      const norm = answer.trim().toLowerCase();
      if (!norm) return resolve(defaultYes);
      if (["j", "ja", "y", "yes"].includes(norm)) return resolve(true);
      if (["n", "nein", "no"].includes(norm)) return resolve(false);
      resolve(defaultYes);
    });
  });
}

function performUpdateAndRestart(targetVersion, rawArgs) {
  const pkgName = PKG_JSON.name || "blackthis";
  const pkgSpec = `${pkgName}@latest`;
  const forwardedArgs = rawArgs.filter((a) => a !== "--update");
  const childEnv = { ...process.env, BLACKTHIS_UPDATED: "1" };

  const normalizedRoot = PKG_ROOT.replace(/\\/g, "/");
  const isNpxCache = normalizedRoot.includes("/_npx/") && normalizedRoot.endsWith(`/node_modules/${pkgName}`);
  const isLocalDevRepo = fs.existsSync(path.join(PKG_ROOT, ".git"));
  const isNodeModulesInstall = normalizedRoot.endsWith(`/node_modules/${pkgName}`);

  console.log(`\nAktualisiere ${pkgName} auf v${targetVersion}...`);

  let updatedInPlace = false;
  if (isNpxCache) {
    // Aktualisiert direkt den aktuellen npx-Cache-Ordner, sodass auch künftiges 'npx blackthis' aktuell ist
    const npxCacheDir = path.resolve(PKG_ROOT, "..", "..");
    const res = runSync(
      NPM_BIN,
      ["install", "--prefix", npxCacheDir, "--no-audit", "--no-fund", pkgSpec],
      { shell: IS_WIN }
    );
    updatedInPlace = res.status === 0;
  } else if (isNodeModulesInstall && !isLocalDevRepo) {
    // Globale Installation in-place aktualisieren
    const res = runSync(
      NPM_BIN,
      ["install", "-g", "--no-audit", "--no-fund", pkgSpec],
      { shell: IS_WIN }
    );
    updatedInPlace = res.status === 0;
  }

  let child;
  if (updatedInPlace) {
    console.log(`Update auf v${targetVersion} abgeschlossen – starte neu...\n`);
    child = spawn(process.execPath, [path.join(PKG_ROOT, "bin", "blackthis.js"), ...forwardedArgs], {
      stdio: "inherit",
      env: childEnv,
    });
  } else {
    // Fallback (z. B. fehlende Schreibrechte oder lokaler Dev-Ordner): direkt per npx @latest starten
    console.log(`Starte ${pkgSpec} direkt über npx...\n`);
    child = spawn(NPX_BIN, ["--yes", pkgSpec, ...forwardedArgs], {
      stdio: "inherit",
      env: childEnv,
      shell: IS_WIN,
    });
  }

  const forwardSignal = (sig) => {
    if (!child.killed) child.kill(sig);
  };
  process.on("SIGINT", () => forwardSignal("SIGINT"));
  process.on("SIGTERM", () => forwardSignal("SIGTERM"));

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });
  return true;
}

async function maybeCheckForUpdates(args) {
  if (
    process.env.BLACKTHIS_UPDATED === "1" ||
    process.env.BLACKTHIS_NO_UPDATE_CHECK === "1" ||
    args.includes("--no-update-check")
  ) {
    return false;
  }

  const forceUpdate = args.includes("--update");
  const pkgName = PKG_JSON.name || "blackthis";
  const latest = await fetchLatestNpmVersion(pkgName);

  if (!latest) {
    if (forceUpdate) {
      console.warn("\nHinweis: Konnte npmjs.com nicht erreichen (offline?) – starte aktuelle Version.\n");
    }
    return false;
  }

  if (!isNewerVersion(latest, PKG_JSON.version)) {
    if (forceUpdate) {
      console.log(`\n${pkgName} v${PKG_JSON.version} ist bereits die aktuellste Version.\n`);
    }
    return false;
  }

  console.log("");
  console.log(`⬆ Neue Version verfügbar: ${pkgName} v${latest} (aktuell: v${PKG_JSON.version})`);

  if (forceUpdate) {
    return performUpdateAndRestart(latest, args);
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    console.log(`  Update-Hinweis: 'npx ${pkgName}@latest' oder 'npm install -g ${pkgName}@latest'\n`);
    return false;
  }

  const shouldUpdate = await askYesNo(`  Jetzt auf v${latest} aktualisieren und neu starten? [J/n] `, true);
  if (shouldUpdate) {
    return performUpdateAndRestart(latest, args);
  }
  console.log(`  Übersprungen – starte v${PKG_JSON.version}.\n`);
  return false;
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
  blackthis --update            Aktualisiert direkt auf die neueste Version von npm
  blackthis --no-update-check   Überspringt die automatische Update-Prüfung beim Start
  blackthis --version           Zeigt die Version an
  blackthis --help              Zeigt diese Hilfe an

Beispiel Terminal-Modus:
  blackthis --cli --input meine_datei.csv --vorname Max --nachname Mustermann
  blackthis --cli --input dokument.pdf --vorname Max --nachname Mustermann
`);
}

async function main() {
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

  const restarted = await maybeCheckForUpdates(args);
  if (restarted) {
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
    const ignored = new Set(["--setup", "--no-model", "--update", "--no-update-check"]);
    const forwarded = args.filter((a, i) => i !== cliIdx && !ignored.has(a));
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
