"""Genera el esqueleto del proyecto reimplementado segun la entrevista.

Filosofia: el esqueleto compila y arranca, pero la logica de negocio son TODOs
que remiten a la evidencia del analisis. Nada se copia del APK original.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .interview import Answers


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "app"


def _camel(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Screen"


def _replace(tpl: str, mapping: dict[str, str]) -> str:
    out = tpl
    for k, v in mapping.items():
        out = out.replace(f"__{k}__", str(v))
    return out


# ---------------------------------------------------------------- Android/Kotlin
ANDROID: dict[str, str] = {
    "settings.gradle.kts": """\
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "__ROOT__"
include(":app")
""",
    "build.gradle.kts": """\
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
""",
    "gradle.properties": """\
org.gradle.jvmargs=-Xmx2048m
android.useAndroidX=true
kotlin.code.style=official
""",
    "app/build.gradle.kts": """\
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "__PKG__"
    compileSdk = 34

    defaultConfig {
        applicationId = "__PKG__"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.compose.ui:ui:1.6.8")
    implementation("androidx.compose.material3:material3:1.2.1")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
""",
    "app/src/main/AndroidManifest.xml": """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <!-- Permisos: solo los que la app nueva realmente usa. Compara con el
         manifest del origen (ver analysis/rea.inspect_android_package.json). -->
    <uses-permission android:name="android.permission.INTERNET" />

    <application
        android:allowBackup="false"
        android:label="__APP_NAME__"
        android:supportsRtl="true"
        android:theme="@style/Theme.Material3.DayNight.NoActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""",
    "app/src/main/java/__PKG_PATH__/MainActivity.kt": """\
package __PKG__

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import __PKG__.ui.AppRoot

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AppRoot() }
    }
}
""",
    "app/src/main/java/__PKG_PATH__/ui/AppRoot.kt": """\
package __PKG__.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Rutas iniciales derivadas de la entrevista. Añade mas en NavHost. */
private val startScreens = listOf(__SCREENS__)

@Composable
fun AppRoot() {
    var current by remember { mutableStateOf(startScreens.first()) }
    MaterialTheme {
        Scaffold { padding ->
            Column(Modifier.padding(padding).padding(16.dp)) {
                Text(current, style = MaterialTheme.typography.headlineMedium)
                Spacer(Modifier.height(12.dp))
                Text(
                    "TODO: reimplementar el flujo de esta pantalla.\\n" +
                    "Evidencia: analysis/ANALYSIS.md y analysis/evidence/",
                    style = MaterialTheme.typography.bodyMedium
                )
                Spacer(Modifier.height(24.dp))
                startScreens.forEach { s ->
                    if (s != current) {
                        OutlinedButton(onClick = { current = s }) { Text("Ir a " + s) }
                        Spacer(Modifier.height(8.dp))
                    }
                }
            }
        }
    }
}
""",
    "app/src/main/java/__PKG_PATH__/data/ApiService.kt": """\
package __PKG__.data

/**
 * Capa de datos. Modo actual: __BACKEND_MODE__.
 * Base URL: __BASE_URL__
 *
 * TODO: sustituye los stubs por las llamadas reales de TU backend.
 * Los endpoints del APK original se documentaron en analysis/ANALYSIS.md;
 * no reutilices los de un tercero.
 */
object ApiService {
    private const val BASE_URL = "__BASE_URL__"

    suspend fun loadHome(): List<Item> {
        // TODO: llamada real o persistencia local
        return listOf(
            Item("1", "Elemento de ejemplo", "Implementa aqui el modelo real")
        )
    }
}

data class Item(val id: String, val title: String, val subtitle: String)
""",
    "README.md": """\
# __APP_NAME__ (Android · Kotlin + Compose)

Reimplementacion generada por `rea-termux` a partir del analisis de
`__SOURCE_PKG__`. **El codigo es un esqueleto nuevo**: no contiene codigo del APK
original.

## Compilar

En Termux no hay Android SDK completo; compila en otra maquina o en la nube:

```bash
# opcion A: Gradle en PC con Android SDK
gradle wrapper && ./gradlew assembleDebug

# opcion B: GitHub Actions (workflow incluido: .github/workflows/android.yml)
```

Instalar el APK resultante en el telefono:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Siguiente paso

1. Lee `../../analysis/ANALYSIS.md`.
2. Elige el flujo #1 y reescribe `ui/AppRoot.kt` pantalla por pantalla.
3. Define tus modelos en `data/ApiService.kt` (modo __BACKEND_MODE__).
""",
    ".github/workflows/android.yml": """\
name: android
on: [push, workflow_dispatch]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: '17' }
      - uses: android-actions/setup-android@v3
      - run: gradle wrapper --gradle-version 8.7
      - run: ./gradlew assembleDebug --no-daemon
      - uses: actions/upload-artifact@v4
        with: { name: app-debug, path: app/build/outputs/apk/debug/app-debug.apk }
""",
}

# ---------------------------------------------------------------------- Flutter
FLUTTER: dict[str, str] = {
    "pubspec.yaml": """\
name: __SLUG__
description: __APP_NAME__ - reimplementacion generada por rea-termux
publish_to: "none"
version: 0.1.0+1

environment:
  sdk: ">=3.4.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter
  http: ^1.2.2
  shared_preferences: ^2.3.2

flutter:
  uses-material-design: true
""",
    "lib/main.dart": """\
import 'package:flutter/material.dart';
import 'app.dart';

void main() => runApp(const __CAMEL__App());
""",
    "lib/app.dart": """\
import 'package:flutter/material.dart';
import 'screens/__FIRST_SCREEN__.dart';

class __CAMEL__App extends StatelessWidget {
  const __CAMEL__App({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '__APP_NAME__',
      theme: ThemeData(colorSchemeSeed: const Color(0xFF3D5AFE), useMaterial3: true),
      home: const __FIRST_CAMEL__Screen(),
    );
  }
}
""",
    "lib/screens/__FIRST_SCREEN__.dart": """\
import 'package:flutter/material.dart';

/// Pantalla inicial. TODO: reimplementar a partir de la evidencia del analisis
/// (analysis/ANALYSIS.md). Nada de este archivo viene del APK original.
class __FIRST_CAMEL__Screen extends StatelessWidget {
  const __FIRST_CAMEL__Screen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('__FIRST_CAMEL__')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: const [
          ListTile(
            title: Text('Esqueleto generado'),
            subtitle: Text('Implementa aqui el flujo real'),
          ),
        ],
      ),
    );
  }
}
""",
    "lib/data/api_client.dart": """\
import 'dart:convert';
import 'package:http/http.dart' as http;

/// Modo: __BACKEND_MODE__  ·  base: __BASE_URL__
class ApiClient {
  ApiClient({this.baseUrl = '__BASE_URL__'});
  final String baseUrl;

  Future<List<Map<String, dynamic>>> loadHome() async {
    // TODO: sustituir por TU backend, o por datos locales si modo = mock
    if (baseUrl.isEmpty) {
      return [
        {'id': '1', 'title': 'Elemento de ejemplo'},
      ];
    }
    final res = await http.get(Uri.parse('$baseUrl/home'));
    if (res.statusCode != 200) {
      throw Exception('HTTP ${res.statusCode}');
    }
    return (jsonDecode(res.body) as List).cast<Map<String, dynamic>>();
  }
}
""",
    "README.md": """\
# __APP_NAME__ (Flutter)

Esqueleto generado por `rea-termux` desde el analisis de `__SOURCE_PKG__`.

```bash
flutter pub get
flutter run            # con el telefono conectado por adb, o
flutter build apk --debug
adb install -r build/app/outputs/flutter-apk/app-debug.apk
```

Siguiente: lee `../../analysis/ANALYSIS.md` y rellena `lib/screens/`.
""",
}

# ----------------------------------------------------------------- React Native
RN: dict[str, str] = {
    "package.json": """\
{
  "name": "__SLUG__",
  "version": "0.1.0",
  "private": true,
  "main": "index.js",
  "scripts": {
    "start": "expo start",
    "android": "expo run:android"
  },
  "dependencies": {
    "expo": "~51.0.0",
    "expo-status-bar": "~1.12.1",
    "react": "18.2.0",
    "react-native": "0.74.5"
  },
  "devDependencies": {
    "typescript": "~5.3.3",
    "@types/react": "~18.2.79"
  }
}
""",
    "app.json": """\
{
  "expo": {
    "name": "__APP_NAME__",
    "slug": "__SLUG__",
    "android": { "package": "__PKG__" },
    "ios": { "bundleIdentifier": "__PKG__" }
  }
}
""",
    "index.js": """\
import { registerRootComponent } from 'expo';
import App from './App';
registerRootComponent(App);
""",
    "App.tsx": """\
import React, { useState } from 'react';
import { SafeAreaView, Text, Button, View, StyleSheet } from 'react-native';

const SCREENS = [__SCREENS_TS__];

export default function App() {
  const [screen, setScreen] = useState(SCREENS[0]);
  return (
    <SafeAreaView style={styles.root}>
      <Text style={styles.title}>{screen}</Text>
      <Text style={styles.hint}>
        TODO: reimplementar este flujo. Evidencia: analysis/ANALYSIS.md
      </Text>
      <View style={styles.row}>
        {SCREENS.filter((s) => s !== screen).map((s) => (
          <Button key={s} title={'Ir a ' + s} onPress={() => setScreen(s)} />
        ))}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 20, gap: 12 },
  title: { fontSize: 24, fontWeight: '600' },
  hint: { color: '#555' },
  row: { gap: 8, marginTop: 16 },
});
""",
    "src/api.ts": """\
/** Modo: __BACKEND_MODE__ · base: __BASE_URL__ */
export const BASE_URL = '__BASE_URL__';

export type Item = { id: string; title: string };

export async function loadHome(): Promise<Item[]> {
  // TODO: TU backend o almacenamiento local
  if (!BASE_URL) return [{ id: '1', title: 'Elemento de ejemplo' }];
  const res = await fetch(`${BASE_URL}/home`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Item[];
}
""",
    "README.md": """\
# __APP_NAME__ (Expo / React Native)

```bash
npm install
npx expo start        # escanea el QR con Expo Go
npx expo run:android  # o compila nativo
```

Esqueleto generado por `rea-termux` desde `__SOURCE_PKG__`.
""",
}

# ---------------------------------------------------------------------- Web PWA
WEB: dict[str, str] = {
    "package.json": """\
{
  "name": "__SLUG__",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "typescript": "^5.5.4", "vite": "^5.4.2" }
}
""",
    "vite.config.ts": """\
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { host: '0.0.0.0', port: 5173 },
});
""",
    "index.html": """\
<!doctype html>
<html lang="__LANG__">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="manifest" href="/manifest.webmanifest" />
    <title>__APP_NAME__</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
""",
    "public/manifest.webmanifest": """\
{
  "name": "__APP_NAME__",
  "short_name": "__APP_NAME__",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#3d5afe",
  "icons": []
}
""",
    "src/main.tsx": """\
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

createRoot(document.getElementById('root')!).render(<App />);
""",
    "src/App.tsx": """\
import React, { useState } from 'react';

const SCREENS = [__SCREENS_TS__];

export default function App() {
  const [screen, setScreen] = useState(SCREENS[0]);
  return (
    <main style={{ fontFamily: 'system-ui', padding: 24, maxWidth: 720, margin: '0 auto' }}>
      <h1>{screen}</h1>
      <p style={{ color: '#555' }}>
        TODO: reimplementar este flujo. Evidencia en analysis/ANALYSIS.md
      </p>
      {SCREENS.filter((s) => s !== screen).map((s) => (
        <button key={s} onClick={() => setScreen(s)} style={{ marginRight: 8 }}>
          Ir a {s}
        </button>
      ))}
    </main>
  );
}
""",
    "src/api.ts": """\
/** Modo: __BACKEND_MODE__ · base: __BASE_URL__ */
export const BASE_URL = '__BASE_URL__';

export async function loadHome() {
  if (!BASE_URL) return [{ id: '1', title: 'Elemento de ejemplo' }];
  const res = await fetch(`${BASE_URL}/home`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
""",
    "README.md": """\
# __APP_NAME__ (PWA)

```bash
npm install
npm run dev -- --host 0.0.0.0
```

Esqueleto generado por `rea-termux` desde `__SOURCE_PKG__`.
""",
}

EXTRA_SCREEN = {
    "flutter": (
        "lib/screens/__SLUG__.dart",
        """\
import 'package:flutter/material.dart';

/// Pantalla __NAME__ (esqueleto). TODO: reimplementar el flujo real a partir
/// de analysis/ANALYSIS.md.
class __CAMEL__Screen extends StatelessWidget {
  const __CAMEL__Screen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('__NAME__')),
      body: const Center(
        child: Text('TODO: implementar __NAME__'),
      ),
    );
  }
}
""",
    ),
    "android-kotlin": (
        "app/src/main/java/__PKG_PATH__/ui/screens/__CAMEL__.kt",
        """\
package __PKG__.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Pantalla __NAME__ (esqueleto). TODO: reimplementar el flujo real. */
@Composable
fun __CAMEL__Screen() {
    Column(Modifier.padding(16.dp)) {
        Text("__NAME__", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))
        Text("TODO: implementar esta pantalla (ver analysis/ANALYSIS.md)")
    }
}
""",
    ),
    "react-native": (
        "src/screens/__CAMEL__.tsx",
        """\
import React from 'react';
import { Text, View } from 'react-native';

/** Pantalla __NAME__ (esqueleto). TODO: reimplementar el flujo real. */
export default function __CAMEL__Screen() {
  return (
    <View style={{ padding: 16, gap: 8 }}>
      <Text style={{ fontSize: 20 }}>__NAME__</Text>
      <Text>TODO: implementar esta pantalla (ver analysis/ANALYSIS.md)</Text>
    </View>
  );
}
""",
    ),
    "web-pwa": (
        "src/screens/__CAMEL__.tsx",
        """\
import React from 'react';

/** Pantalla __NAME__ (esqueleto). TODO: reimplementar el flujo real. */
export default function __CAMEL__Screen() {
  return (
    <section>
      <h2>__NAME__</h2>
      <p>TODO: implementar esta pantalla (ver analysis/ANALYSIS.md)</p>
    </section>
  );
}
""",
    ),
}

STACK_TEMPLATES = {
    "android-kotlin": ANDROID,
    "flutter": FLUTTER,
    "react-native": RN,
    "web-pwa": WEB,
}


def write_project(
    answers: Answers,
    out_dir: Path,
    *,
    source_package: str,
) -> list[Path]:
    tpl = STACK_TEMPLATES.get(answers.stack)
    if tpl is None:
        raise ValueError(f"stack desconocido: {answers.stack}")

    screens = answers.screens or ["home"]
    first = screens[0]
    pkg = answers.package_id or "com.example.app"
    base_url = answers.backend_base_url or ("" if answers.backend == "mock" else "")
    mapping = {
        "APP_NAME": answers.app_name or "MiApp",
        "SLUG": _slug(answers.app_name or "miapp"),
        "CAMEL": _camel(answers.app_name or "MiApp"),
        "ROOT": _slug(answers.app_name or "miapp"),
        "PKG": pkg,
        "PKG_PATH": pkg.replace(".", "/"),
        "SOURCE_PKG": source_package,
        "BACKEND_MODE": answers.backend,
        "BASE_URL": base_url,
        "LANG": answers.ui_language or "es",
        "SCREENS": ", ".join(f'"{s}"' for s in screens),
        "SCREENS_TS": ", ".join(f"'{s}'" for s in screens),
        "FIRST_SCREEN": _slug(first),
        "FIRST_CAMEL": _camel(first),
    }

    written: list[Path] = []
    for rel, content in tpl.items():
        real = _replace(rel, mapping)
        path = out_dir / real
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_replace(content, mapping), encoding="utf-8")
        written.append(path)

    # una pantalla extra por cada flujo pedido (la primera ya viene en la base)
    extra = EXTRA_SCREEN.get(answers.stack)
    if extra:
        tpl_path, tpl_body = extra
        for sc in screens[1:]:
            sub = dict(mapping, NAME=sc, SLUG=_slug(sc), CAMEL=_camel(sc))
            path = out_dir / _replace(tpl_path, sub)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_replace(tpl_body, sub), encoding="utf-8")
            written.append(path)

    next_steps = out_dir / "NEXT_STEPS.md"
    next_steps.write_text(
        f"""# Siguiente paso ({answers.stack})

Este arbol se genero desde la entrevista y el analisis de `{source_package}`.

- App: **{answers.app_name}** · package/bundle: `{pkg}`

1. Lee `../../analysis/ANALYSIS.md` (hallazgos con evidencia).
2. Revisa `../../CLONE_PLAN.md` — es el contrato de lo que vas a construir.
3. Implementa pantalla por pantalla, empezando por
   `{', '.join(screens)}`.
4. Sustituye los `TODO` de la capa de datos (modo `{answers.backend}`).
5. Recursos propios: nombre, iconos, tipografias y textos NO se copian del
   original.

Archivos generados: {len(written)}
""",
        encoding="utf-8",
    )
    written.append(next_steps)
    return written
