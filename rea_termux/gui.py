"""Panel web local para ejecutar rea-termux desde el navegador del telefono.

El servidor solo enlaza loopback por defecto y expone acciones allowlisted; nunca
acepta comandos arbitrarios desde el navegador. Implementado con la biblioteca
estandar para funcionar en Termux sin Node, frameworks web ni CDN.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import uicapture
from .config import load as load_config

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_LOG_LINES = 1800


# UI autocontenida: no carga fuentes, scripts ni estilos externos.
PAGE = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#10131d"><title>rea-termux · Panel</title>
<style>
:root{color-scheme:dark;--bg:#10131d;--panel:#191e2b;--panel2:#202738;--line:#30394c;--text:#f2f5fb;--muted:#aab4c6;--accent:#79e0c0;--blue:#91b6ff;--warn:#ffd37e;--red:#ff9595;--radius:18px;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 5% 0%,#1b3442 0,transparent 42%),var(--bg);color:var(--text)}button,input,select,textarea{font:inherit}button{cursor:pointer;border:0;border-radius:12px;background:var(--accent);color:#09251e;font-weight:750;padding:12px 16px;min-height:46px}button.secondary{background:#2a3446;color:var(--text);border:1px solid var(--line)}button.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}button:disabled{opacity:.45;cursor:not-allowed}.wrap{max-width:1100px;margin:auto;padding:18px 16px 72px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 0 24px}.brand{display:flex;gap:12px;align-items:center}.mark{width:42px;height:42px;border-radius:14px;background:linear-gradient(140deg,#9ff0d6,#81aaff);display:grid;place-items:center;color:#091b20;font-weight:900;font-size:20px}.brand h1{font-size:19px;margin:0}.brand small{display:block;color:var(--muted);font-size:12px}.status-pill{border:1px solid var(--line);border-radius:99px;padding:7px 11px;color:var(--muted);font-size:12px}.hero{padding:20px 0 22px}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:11px}.hero h2{font-size:clamp(25px,5vw,42px);line-height:1.08;letter-spacing:-.035em;max-width:730px;margin:8px 0}.hero p{color:var(--muted);max-width:680px;margin:10px 0 0}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}.card{background:linear-gradient(160deg,rgba(31,39,55,.96),rgba(24,29,42,.97));border:1px solid var(--line);border-radius:var(--radius);padding:18px;min-width:0}.span8{grid-column:span 8}.span4{grid-column:span 4}.span12{grid-column:span 12}.card h3{margin:0 0 4px;font-size:17px}.sub{color:var(--muted);font-size:13px;margin:0 0 16px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.stack{display:grid;gap:12px}.field{display:grid;gap:6px;min-width:0}.field label{font-size:13px;font-weight:650;color:#dfe6f3}.field small,.hint{color:var(--muted);font-size:12px}.field input,.field select,.field textarea{width:100%;background:#111722;border:1px solid #3a455a;border-radius:11px;color:var(--text);padding:11px 12px;outline:none}.field input:focus,.field select:focus,.field textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px #79e0c022}.field textarea{min-height:80px;resize:vertical}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:12px}.check{display:flex;gap:10px;align-items:flex-start;color:var(--muted);font-size:13px}.check input{accent-color:var(--accent);margin-top:4px}.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:6px 0 16px}.step{padding:9px 8px;border:1px solid var(--line);border-radius:11px;text-align:center;color:#8e9aaf;font-size:11px}.step b{display:block;font-size:14px;margin-bottom:2px}.step.active{border-color:#79e0c077;background:#79e0c011;color:var(--accent)}.step.done{color:#b6f2df;border-color:#79e0c044}.log{background:#0b0f17;border:1px solid #262e3c;border-radius:12px;padding:12px;max-height:245px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#c6d0e1;font:12px/1.55 ui-monospace,SFMono-Regular,monospace}.chip{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--line);border-radius:999px;padding:5px 9px;color:var(--muted);font-size:12px;margin:3px 4px 3px 0}.chip.good{color:var(--accent);border-color:#79e0c044}.chip.warn{color:var(--warn);border-color:#ffd37e44}.empty{color:var(--muted);padding:18px;border:1px dashed #445069;border-radius:13px;text-align:center}.hidden{display:none!important}.report{max-height:440px;overflow:auto;background:#101621;border:1px solid var(--line);border-radius:12px;padding:14px;white-space:pre-wrap;font:13px/1.55 ui-monospace,monospace;color:#d7dfec}.screens{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.screen{background:#111722;border:1px solid var(--line);border-radius:12px;padding:9px;overflow:hidden}.screen img{width:100%;height:250px;object-fit:contain;background:#080b11;border-radius:8px}.screen strong{display:block;margin:6px 0 0;overflow-wrap:anywhere}.drop{border:1px dashed #596781;border-radius:14px;padding:16px;text-align:center;background:#151c29}.drop.drag{border-color:var(--accent);background:#79e0c010}.chatbox{height:250px;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:10px;background:#111722;display:flex;flex-direction:column;gap:8px}.bubble{max-width:92%;padding:10px 12px;border-radius:13px;background:#232c3c;white-space:pre-wrap;overflow-wrap:anywhere}.bubble.me{align-self:flex-end;background:#16382f;color:#d7fff0}.bubble.agent{align-self:flex-start}.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#ecf5ff;color:#101722;padding:11px 16px;border-radius:12px;box-shadow:0 10px 30px #0008;z-index:10;max-width:92vw}.muted{color:var(--muted)}.goodtxt{color:var(--accent)}.warntxt{color:var(--warn)}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}.file-name{max-width:100%;overflow-wrap:anywhere;color:var(--blue)}.divider{height:1px;background:var(--line);margin:14px 0}.footer{text-align:center;color:#7e8ba2;font-size:12px;margin-top:18px}.inline{display:flex;gap:8px}.inline input{flex:1;min-width:0}
@media(max-width:760px){.span8,.span4{grid-column:span 12}.wrap{padding:12px 12px 56px}.top{padding-bottom:12px}.card{padding:15px}.steps{grid-template-columns:repeat(5,minmax(55px,1fr));overflow:auto}.step{font-size:10px;padding:8px 4px}.step b{font-size:12px}.twocol{grid-template-columns:1fr}.hero{padding-top:10px}.screen img{height:210px}}
</style></head><body><div class="wrap">
<header class="top"><div class="brand"><div class="mark">R</div><div><h1>rea-termux</h1><small>Panel local · asistente de reimplementación</small></div></div><div id="serverStatus" class="status-pill">Conectando…</div></header>
<section class="hero"><div class="eyebrow">Tu laboratorio de apps</div><h2>De un APK a un proyecto propio, paso a paso.</h2><p>Sube el APK, revisa el análisis, captura las pantallas que te interesan y define el nuevo producto. El panel ejecuta el proceso y se detiene antes de generar para que revises el plan.</p></section>
<nav class="steps" aria-label="Etapas"><div class="step" data-step="start"><b>1</b>APK</div><div class="step" data-step="analysis"><b>2</b>Análisis</div><div class="step" data-step="design"><b>3</b>Pantallas</div><div class="step" data-step="interview"><b>4</b>Entrevista</div><div class="step" data-step="build"><b>5</b>Proyecto</div></nav>
<main class="grid">
<section class="card span8" id="newProject"><h3>Crear proyecto</h3><p class="sub">Una sola vez: elige el APK y confirma que crearás una implementación con identidad y recursos propios.</p>
<div class="stack"><div class="drop" id="drop"><input id="apkFile" type="file" accept=".apk,application/vnd.android.package-archive" hidden><button class="secondary" id="pickFile">Elegir APK</button><div class="hint" style="margin-top:8px">o arrástralo aquí · no se instala ni se ejecuta</div><div id="fileName" class="file-name" style="margin-top:7px"></div></div>
<div class="twocol"><div class="field"><label for="projectName">Nombre del proyecto</label><input id="projectName" maxlength="48" placeholder="Mi proyecto"></div><div class="field"><label for="packageId">Nuevo package ID</label><input id="packageId" maxlength="150" placeholder="com.example.miapp"><small>No reutilices el identificador de la app de referencia.</small></div></div>
<label class="check"><input id="attest" type="checkbox"><span>Confirmo que reimplementaré el comportamiento con nombre, package, código y recursos propios; no copiaré código ni assets del APK.</span></label>
<div class="actions"><button id="startBtn" disabled>Subir y analizar</button><button class="ghost" id="doctorBtn">Revisar instalación</button></div></div></section>
<section class="card span4"><h3>Preparación</h3><p class="sub">Estado básico del entorno y los proveedores.</p><div id="readiness" class="stack"><div class="empty">Consultando configuración…</div></div><div class="divider"></div><p class="hint">El panel usa las credenciales desde las variables de entorno configuradas en Termux. Nunca las muestra.</p></section>
<section class="card span12 hidden" id="progressCard"><h3 id="jobTitle">Actividad</h3><p class="sub" id="jobSubtitle">—</p><div id="jobLog" class="log" aria-live="polite"></div></section>
<section class="card span12 hidden" id="analysisCard"><h3>Informe del análisis</h3><p class="sub">Evidencia y conclusiones obtenidas para esta app de referencia.</p><div id="analysisText" class="report"></div></section>
<section class="card span12 hidden" id="screensCard"><h3>Capturar pantallas de referencia</h3><p class="sub">Conecta el teléfono por ADB, navega hasta la pantalla deseada y pulsa capturar. Cada captura guarda PNG, jerarquía y wireframe.</p><div class="row"><span id="deviceStatus" class="chip">Comprobando ADB…</span><button class="secondary" id="refreshDevices">Buscar teléfono</button></div><div class="twocol" style="margin-top:12px"><div class="field"><label for="screenName">Nombre de la pantalla</label><input id="screenName" maxlength="48" placeholder="home, login, perfil…"></div><div class="field"><label for="deviceSelect">Dispositivo</label><select id="deviceSelect"><option value="">Auto</option></select></div></div><div class="actions"><button id="captureBtn" class="secondary">Capturar pantalla actual</button><button id="visionBtn" class="ghost" title="La captura visual con IA puede enviar la imagen al proveedor configurado">Capturar + análisis visual opcional</button></div><div class="screens" id="screenGallery" style="margin-top:14px"></div></section>
<section class="card span12 hidden" id="interviewCard"><h3>Define tu versión</h3><p class="sub">Esta entrevista ocurre antes de generar código. Los valores iniciales se sugieren a partir del análisis y puedes cambiarlos.</p><div class="stack">
<div class="twocol"><div class="field"><label for="appName">Nombre de la nueva app</label><input id="appName" maxlength="80"></div><div class="field"><label for="newPackage">Package / bundle ID propio</label><input id="newPackage" maxlength="150"></div></div>
<div class="field"><label for="purpose">Propósito en una frase</label><input id="purpose" maxlength="240" placeholder="Qué necesidad resuelve la app"></div>
<div class="field"><label for="improvements">¿Qué cambiar o mejorar?</label><textarea id="improvements" placeholder="Accesibilidad, navegación, aspecto, funciones…"></textarea></div>
<div class="twocol"><div class="field"><label for="stack">Tecnología</label><select id="stack"><option value="android-kotlin">Android nativo · Kotlin + Compose</option><option value="flutter">Flutter</option><option value="react-native">React Native / Expo</option><option value="web-pwa">Web PWA</option></select></div><div class="field"><label for="screensInput">Pantallas prioritarias</label><input id="screensInput" placeholder="login, home, perfil"></div></div>
<div class="twocol"><div class="field"><label for="backend">Backend</label><select id="backend"><option value="mock">Mock local · recomendado</option><option value="same-api">API existente (solo si tienes autorización)</option><option value="new-api">API nueva propia</option></select></div><div class="field"><label for="baseUrl">URL base del backend (si aplica)</label><input id="baseUrl" placeholder="https://api.ejemplo.com"></div></div>
<div class="twocol"><div class="field"><label for="language">Idioma</label><input id="language" value="es"></div><div class="field"><label for="monetization">Monetización</label><select id="monetization"><option>ninguna</option><option>ads</option><option>suscripcion</option></select></div></div>
<div class="twocol"><label class="check"><input id="offline" type="checkbox" checked><span>Debe funcionar sin conexión cuando sea posible.</span></label><div class="field"><label for="removeFeatures">Funciones que quieres quitar</label><input id="removeFeatures" placeholder="ninguna"></div></div>
<div class="field"><label for="notes">Otras indicaciones</label><textarea id="notes" placeholder="Requisitos, límites, preferencias…"></textarea></div>
<div class="actions"><button id="planBtn">Preparar plan para revisar</button></div></div></section>
<section class="card span12 hidden" id="planCard"><h3>Revisa el plan antes de construir</h3><p class="sub">Comprueba identidad, stack, pantallas, backend y mejoras. No se generará el proyecto hasta que pulses “Aprobar y generar”.</p><div id="planText" class="report"></div><div class="actions"><button id="editPlanBtn" class="secondary">Volver a la entrevista</button><button id="buildBtn">Aprobar y generar proyecto</button></div></section>
<section class="card span8 hidden" id="chatCard"><h3>Continuar conversando</h3><p class="sub">Pide cambios, nuevas pantallas o mejoras. La sesión y memoria del proyecto se guardan en el workspace.</p><div id="chatMessages" class="chatbox"></div><div class="inline" style="margin-top:10px"><input id="chatInput" placeholder="Ej.: mejora el formulario de acceso…" maxlength="5000"><button id="sendChat">Enviar</button></div></section>
<section class="card span4 hidden" id="workspaceCard"><h3>Workspace</h3><p class="sub">Estado y acciones del proyecto.</p><div id="workspaceInfo" class="stack"></div><div class="actions"><button id="verifyBtn" class="secondary">Verificar proyecto</button><button id="statusBtn" class="ghost">Actualizar estado</button></div></section>
</main><p class="footer">Servidor local · los APKs se analizan en el dispositivo · evita capturas con datos personales</p></div><div id="toast" class="toast hidden"></div>
<script>
const token=new URLSearchParams(location.search).get('token')||'';let selectedFile=null,currentJob=null,lastChat='';
const $=id=>document.getElementById(id);const esc=s=>String(s??'');
function toast(s){$('toast').textContent=s;$('toast').classList.remove('hidden');setTimeout(()=>$('toast').classList.add('hidden'),3600)}
async function api(path,opts={}){let headers={...(opts.headers||{}),'X-Rea-Token':token};let r=await fetch(path,{...opts,headers});let d=await r.json().catch(()=>({error:'Respuesta inválida'}));if(!r.ok)throw Error(d.error||'Error '+r.status);return d}
function show(id){$(id).classList.remove('hidden')}function setStep(name){document.querySelectorAll('.step').forEach(x=>{let order=['start','analysis','design','interview','build'];x.classList.toggle('active',x.dataset.step===name);x.classList.toggle('done',order.indexOf(x.dataset.step)<order.indexOf(name))})}
function lines(el,arr){el.textContent=(arr||[]).join('\n');el.scrollTop=el.scrollHeight}
function screenUrl(path){return '/api/asset?path='+encodeURIComponent(path)+'&token='+encodeURIComponent(token)}
function renderScreens(items){$('screenGallery').innerHTML='';for(const s of items||[]){let card=document.createElement('div');card.className='screen';let img=document.createElement('img');img.src=screenUrl(s.png);img.alt='Captura '+s.name;let b=document.createElement('strong');b.textContent=s.name;let link=document.createElement('a');link.href=screenUrl(s.svg);link.target='_blank';link.rel='noopener';link.textContent='Abrir wireframe';link.style.color='var(--blue)';card.append(img,b,link);$('screenGallery').append(card)}}
function renderReadiness(data){let el=$('readiness');el.replaceChildren();for(const p of data.providers||[]){let d=document.createElement('div');let chip=document.createElement('span');chip.className='chip '+(p.key_available?'good':'warn');chip.textContent=(p.key_available?'● ':'○ ')+p.name;let hint=document.createElement('span');hint.className='hint';hint.textContent=(p.model||'modelo sin definir')+' · '+(p.key_available?'clave configurada':'falta clave');d.append(chip,hint);el.append(d)}if(!(data.providers||[]).length){let e=document.createElement('div');e.className='empty';e.textContent='No hay proveedores configurados. Ejecuta init una vez y configura 9router/Omniroute.';el.append(e)}let apk=document.createElement('div');apk.className='chip '+(data.android_tools.length?'good':'warn');apk.textContent=data.android_tools.length?'Herramientas Android: '+data.android_tools.join(', '):'Sin herramientas Android locales';el.append(apk);let adb=document.createElement('div');adb.className='chip '+(data.adb?'good':'warn');adb.textContent=data.adb?'ADB disponible':'ADB no detectado';el.append(adb)}
async function refresh(){
 try{
  let s=await api('/api/state');
  $('serverStatus').textContent='Panel local activo';renderReadiness(s.readiness);
  if(s.workspace){
   show('workspaceCard');$('workspaceInfo').replaceChildren();
   let p=document.createElement('div');p.className='chip good';p.textContent=s.workspace;
   let st=document.createElement('div');st.className='hint';st.textContent=s.stage||'Análisis en curso';$('workspaceInfo').append(p,st);
   if(s.suggested_name&&!$('appName').value)$('appName').value=s.suggested_name;
   if(s.suggested_package&&!$('newPackage').value)$('newPackage').value=s.suggested_package;
   if(s.analysis){show('analysisCard');$('analysisText').textContent=s.analysis;setStep(s.analysis_complete?'design':'analysis')}
   if(s.interview_ready||s.plan){
    show('screensCard');show('interviewCard');show('chatCard');renderScreens(s.screens);
    $('screensInput').value=$('screensInput').value||s.screens.map(x=>x.name).join(', ');
    if(s.plan){show('planCard');$('planText').textContent=s.plan;
     if(s.built){$('buildBtn').disabled=true;$('buildBtn').textContent='Ya generado · continúa en el chat';setStep('build')}
     else{$('buildBtn').disabled=false;$('buildBtn').textContent='Aprobar y generar proyecto';setStep('build')}
    }else setStep('interview');
   }else if(s.analysis)setStep(s.analysis_complete?'design':'analysis');
  }
  if(s.job){currentJob=s.job.id;pollJob(currentJob)}
 }catch(e){$('serverStatus').textContent='No conecta';toast(e.message)}
}
async function pollJob(id){try{let j=await api('/api/jobs/'+id);show('progressCard');$('jobTitle').textContent=j.title||'Actividad';$('jobSubtitle').textContent=j.status==='running'?'En curso · '+(j.phase||'procesando'):j.status==='done'?'Terminado':'Estado: '+j.status;lines($('jobLog'),j.output||[]);if(j.status==='running'){setTimeout(()=>pollJob(id),1200)}else{toast(j.status==='done'?'Proceso terminado':'Proceso terminado con errores');await refresh()}}catch(e){toast(e.message)}}
$('pickFile').onclick=()=>$('apkFile').click();$('apkFile').onchange=e=>setFile(e.target.files[0]);function setFile(f){selectedFile=f||null;$('fileName').textContent=f?f.name+' · '+Math.round(f.size/1024/1024)+' MB':'';$('startBtn').disabled=!f||!$('attest').checked;if(f&&!$('projectName').value)$('projectName').value=f.name.replace(/\.apk$/i,'').replace(/[_-]+/g,' ')}$('attest').onchange=()=>{$('startBtn').disabled=!selectedFile||!$('attest').checked};$('drop').ondragover=e=>{e.preventDefault();$('drop').classList.add('drag')};$('drop').ondragleave=()=>$('drop').classList.remove('drag');$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('drag');setFile(e.dataTransfer.files[0])};
$('startBtn').onclick=async()=>{if(!selectedFile||!$('attest').checked)return;try{$('startBtn').disabled=true;$('startBtn').textContent='Subiendo…';let u=await fetch('/api/upload',{method:'POST',headers:{'X-Rea-Token':token,'X-Filename':encodeURIComponent(selectedFile.name),'Content-Type':'application/vnd.android.package-archive'},body:selectedFile});let ud=await u.json();if(!u.ok)throw Error(ud.error||'No se pudo subir');let d=await api('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:ud.upload_id,project_name:$('projectName').value,package_id:$('packageId').value})});currentJob=d.job_id;show('progressCard');setStep('analysis');pollJob(currentJob)}catch(e){toast(e.message);$('startBtn').disabled=false}finally{$('startBtn').textContent='Subir y analizar'}};
$('doctorBtn').onclick=async()=>{try{let d=await api('/api/doctor',{method:'POST'});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Diagnóstico del entorno';pollJob(currentJob)}catch(e){toast(e.message)}};
$('refreshDevices').onclick=async()=>{try{let d=await api('/api/devices');let sel=$('deviceSelect');sel.innerHTML='<option value="">Auto</option>';for(const v of d.devices){let o=document.createElement('option');o.value=v;o.textContent=v;sel.append(o)}$('deviceStatus').textContent=d.devices.length?d.devices.length+' dispositivo(s) autorizado(s)':'No hay dispositivo ADB autorizado';$('deviceStatus').className='chip '+(d.devices.length?'good':'warn')}catch(e){toast(e.message)}};
async function capture(vision){if(!$('screenName').value.trim())return toast('Escribe un nombre para la pantalla');try{let d=await api('/api/capture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('screenName').value,serial:$('deviceSelect').value,vision})});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Captura de pantalla';setStep('design');pollJob(currentJob)}catch(e){toast(e.message)}}$('captureBtn').onclick=()=>capture(false);$('visionBtn').onclick=()=>{if(confirm('La captura se enviará al proveedor multimodal configurado. ¿Continuar?'))capture(true)};
$('planBtn').onclick=async()=>{let data={app_name:$('appName').value,package_id:$('newPackage').value,purpose:$('purpose').value,improvements:$('improvements').value,stack:$('stack').value,screens:$('screensInput').value.split(',').map(x=>x.trim()).filter(Boolean),backend:$('backend').value,backend_base_url:$('baseUrl').value,keep_offline:$('offline').checked,ui_language:$('language').value,remove_features:$('removeFeatures').value||'ninguna',monetization:$('monetization').value,notes:$('notes').value};try{let d=await api('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Preparando el plan';setStep('interview');pollJob(currentJob)}catch(e){toast(e.message)}};
$('editPlanBtn').onclick=()=>{show('interviewCard');$('planCard').classList.add('hidden');setStep('interview')};$('buildBtn').onclick=async()=>{if(!confirm('¿Aprobar este plan y generar el proyecto?'))return;try{let d=await api('/api/build',{method:'POST',body:JSON.stringify({approved:true})});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Generando proyecto';setStep('build');pollJob(currentJob)}catch(e){toast(e.message)}};
$('sendChat').onclick=async()=>{let msg=$('chatInput').value.trim();if(!msg)return;let u=document.createElement('div');u.className='bubble me';u.textContent=msg;$('chatMessages').append(u);$('chatInput').value='';try{let d=await api('/api/chat',{method:'POST',body:JSON.stringify({message:msg})});currentJob=d.job_id;lastChat=msg;show('progressCard');$('jobTitle').textContent='El agente está trabajando';pollChat(currentJob)}catch(e){toast(e.message)}};$('chatInput').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('sendChat').click()}};async function pollChat(id){try{let j=await api('/api/jobs/'+id);lines($('jobLog'),j.output||[]);if(j.status==='running'){setTimeout(()=>pollChat(id),1300);return}let out=(j.output||[]).join('\n');let b=document.createElement('div');b.className='bubble agent';b.textContent=out|| (j.status==='done'?'Listo. Consulta el estado del proyecto.':'El turno terminó con error.');$('chatMessages').append(b);$('chatMessages').scrollTop=$('chatMessages').scrollHeight;await refresh()}catch(e){toast(e.message)}};
$('verifyBtn').onclick=async()=>{try{let d=await api('/api/verify',{method:'POST'});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Verificando proyecto';pollJob(currentJob)}catch(e){toast(e.message)}};$('statusBtn').onclick=async()=>{try{let d=await api('/api/status',{method:'POST'});currentJob=d.job_id;show('progressCard');$('jobTitle').textContent='Actualizando estado';pollJob(currentJob)}catch(e){toast(e.message)}};
refresh();$('refreshDevices').click();
</script></body></html>'''
# La página se mantiene embebida en una cadena raw para conservar los escapes
# JavaScript; convierte los separadores de maquetación fuera del script a newlines.
_page_head, _page_rest = PAGE.split("<script>", 1)
_page_script, _page_tail = _page_rest.split("</script>", 1)
PAGE = (_page_head.replace("\\n", "\n") + "<script>" + _page_script
        + "</script>" + _page_tail.replace("\\n", "\n"))


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-_")
    return (slug[:64] or "rea-app")


def _safe_screen(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip()).strip("_.-")[:48]


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _load_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("La solicitud supera el tamaño permitido")
    data = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(data, dict):
        raise ValueError("Se esperaba un objeto JSON")
    return data


def _child_command(args: tuple[str, ...] | list[str], *, config_path: str | None = None,
                   frozen: bool | None = None, executable: str | None = None) -> list[str]:
    """Construye argv tanto para Python instalado como para el binario congelado."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = executable or sys.executable
    cmd = [exe] if is_frozen else [exe, "-m", "rea_termux"]
    if config_path:
        cmd.extend(["--config", str(Path(config_path).expanduser())])
    cmd.extend(args)
    return cmd


def run_gui(*, config_path: str | None = None, port: int = 8765,
            open_browser: bool = True) -> int:
    """Arranca el panel local; bloquea hasta Ctrl-C."""
    cfg = load_config(config_path)
    ws_root = cfg.ws().resolve()
    inbox = ws_root / ".rea-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    state: dict[str, Any] = {
        "workspace": None, "apk": None, "upload_ids": {}, "jobs": {},
        "current_job": None, "interview_ready": False, "analysis_complete": False,
        "plan_ready": False, "suggested_name": "", "suggested_package": "",
        "lock": threading.RLock(), "config_path": config_path,
    }
    # Al reabrir el panel, retoma el workspace analizado más recientemente.
    candidates = sorted((p for p in ws_root.iterdir()
                         if p.is_dir() and not p.name.startswith(".")
                         and (p / "analysis" / "ANALYSIS.md").is_file()),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        latest = candidates[0].resolve()
        state["workspace"] = str(latest)
        state["analysis_complete"] = True
        state["interview_ready"] = not (latest / "CLONE_PLAN.md").is_file()
        answer_path = latest / "answers.json"
        try:
            saved_answers = json.loads(answer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved_answers = {}
        state["suggested_name"] = saved_answers.get("app_name", latest.name)
        state["suggested_package"] = saved_answers.get("package_id", "")

    def child_args(*args: str) -> list[str]:
        return _child_command(args, config_path=config_path)

    def start_job(title: str, actions: list[tuple[str, list[str]]], *,
                  on_success: Any = None, cleanup: Path | None = None) -> dict[str, str]:
        job_id = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {"id": job_id, "title": title, "status": "running",
                               "phase": "iniciando", "output": [], "returncode": None,
                               "created": time.time()}
        with state["lock"]:
            state["jobs"][job_id] = job
            state["current_job"] = job_id

        def worker() -> None:
            success = True
            try:
                for label, command in actions:
                    job["phase"] = label
                    job["output"].append(f"\n── {label} ──")
                    proc = subprocess.Popen(command, cwd=str(Path.cwd()),
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace",
                                            bufsize=1)
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        with state["lock"]:
                            job["output"].append(line.rstrip("\r\n"))
                            if len(job["output"]) > MAX_LOG_LINES:
                                del job["output"][:len(job["output"]) - MAX_LOG_LINES]
                    rc = proc.wait()
                    if rc != 0:
                        success = False
                        job["output"].append(f"Proceso terminó con código {rc}.")
                        break
                job["returncode"] = 0 if success else (rc if "rc" in locals() else 1)
                if success:
                    if on_success:
                        on_success()
                    job["status"] = "done"
                    job["phase"] = "completado"
                else:
                    job["status"] = "error"
                    job["phase"] = "revisar el registro"
            except Exception as exc:
                job["status"] = "error"
                job["phase"] = "error interno"
                job["output"].append(f"{type(exc).__name__}: {exc}")
            finally:
                job["finished"] = time.time()
                if cleanup:
                    try:
                        cleanup.unlink(missing_ok=True)
                    except OSError:
                        pass

        threading.Thread(target=worker, name=f"rea-gui-{job_id}", daemon=True).start()
        return {"job_id": job_id}

    def current_ws() -> Path:
        raw = state.get("workspace")
        if not raw:
            raise ValueError("Primero crea un proyecto y analiza un APK")
        return _inside(Path(raw), ws_root)

    def readiness() -> dict[str, Any]:
        providers = [{"name": p.name, "model": p.model,
                      "key_available": bool(p.resolve_key())}
                     for p in cfg.provider_chain()]
        android_names = [n for n in ("apktool", "aapt", "aapt2", "apksigner", "zipalign",
                                     "adb", "gradle") if shutil.which(n)]
        return {"providers": providers, "android_tools": android_names,
                "adb": bool(uicapture.adb_path())}

    class Handler(BaseHTTPRequestHandler):
        server_version = "rea-termux-panel/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            # No imprimir query string/token en consola.
            print("[panel] " + (fmt % args).split("?")[0])

        def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-Rea-Token", "")
            if not supplied:
                supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            if not secrets.compare_digest(supplied, token):
                self._json(403, {"error": "Sesión local no autorizada. Recarga el panel."})
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                host, port_ = self.server.server_address
                expected_host = "127.0.0.1" if host in ("127.0.0.1", "localhost") else host
                expected_port = str(port_)
                if parsed.hostname not in (expected_host, "localhost") or parsed.port != int(expected_port):
                    self._json(403, {"error": "Origen no permitido"})
                    return False
            return True

        def _read_body(self, limit: int = MAX_JSON_BYTES) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length inválido") from exc
            if length < 0 or length > limit:
                raise ValueError("Tamaño de solicitud excede el límite")
            return self.rfile.read(length)

        def _body_json(self) -> dict[str, Any]:
            return _load_json(self._read_body())

        def do_GET(self) -> None:
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/devices":
                self._json(200, {"devices": uicapture.devices()})
                return
            if path == "/api/state":
                ws = None
                analysis_text = ""
                plan_text = ""
                screens: list[dict[str, str]] = []
                if state.get("workspace"):
                    try:
                        ws = current_ws()
                        analysis = ws / "analysis" / "ANALYSIS.md"
                        plan = ws / "CLONE_PLAN.md"
                        if analysis.is_file():
                            analysis_text = analysis.read_text(encoding="utf-8", errors="replace")[:12000]
                        if plan.is_file():
                            plan_text = plan.read_text(encoding="utf-8", errors="replace")[:18000]
                        for name in uicapture.list_screens(ws):
                            screens.append({"name": name, "png": f"ui/{name}.png",
                                            "svg": f"ui/{name}.wireframe.svg"})
                    except (ValueError, OSError):
                        ws = None
                with state["lock"]:
                    current_job = state.get("current_job")
                    job = state["jobs"].get(current_job) if current_job else None
                    job_data = ({"id": job["id"], "status": job["status"]}
                                if job and job["status"] == "running" else None)
                    data = {"workspace": str(ws) if ws else None,
                            "stage": "plan aprobado pendiente de generación" if plan_text else
                                     "entrevista pendiente" if state.get("analysis_complete") else "",
                            "analysis": analysis_text, "plan": plan_text,
                            "analysis_complete": bool(state.get("analysis_complete")),
                            "interview_ready": bool(state.get("analysis_complete")) and not bool(plan_text),
                            "suggested_name": state.get("suggested_name", ""),
                            "suggested_package": state.get("suggested_package", ""),
                            "screens": screens, "readiness": readiness(),
                            "built": bool(ws and (ws / "app").is_dir() and any((ws / "app").iterdir())),
                            "job": job_data}
                self._json(200, data)
                return
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                with state["lock"]:
                    job = state["jobs"].get(job_id)
                    if not job:
                        self._json(404, {"error": "Trabajo no encontrado"})
                    else:
                        self._json(200, {k: job.get(k) for k in
                                         ("id", "title", "status", "phase", "output", "returncode")})
                return
            if path == "/api/asset":
                values = parse_qs(parsed.query)
                rel = values.get("path", [""])[0]
                try:
                    ws = current_ws()
                    asset = _inside(ws / rel, ws)
                    if asset.suffix.lower() not in (".png", ".svg") or not asset.is_file():
                        raise ValueError("Recurso no disponible")
                    ctype = "image/svg+xml; charset=utf-8" if asset.suffix.lower() == ".svg" else "image/png"
                    self._send(200, asset.read_bytes(), ctype)
                except (ValueError, OSError):
                    self._json(404, {"error": "Recurso no disponible"})
                return
            self._json(404, {"error": "Ruta no encontrada"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/upload":
                    self._upload()
                elif path == "/api/start":
                    self._start()
                elif path == "/api/doctor":
                    self._json(200, start_job("Diagnóstico", [("doctor", child_args("doctor"))]))
                elif path == "/api/devices":
                    self._json(200, {"devices": uicapture.devices()})
                elif path == "/api/capture":
                    self._capture()
                elif path == "/api/plan":
                    self._plan()
                elif path == "/api/build":
                    self._build()
                elif path == "/api/chat":
                    self._chat()
                elif path == "/api/verify":
                    self._verify()
                elif path == "/api/status":
                    self._status()
                else:
                    self._json(404, {"error": "Ruta no encontrada"})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": f"Error interno: {type(exc).__name__}: {exc}"})

        def _upload(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                raise ValueError("El APK está vacío o supera 1 GB")
            name = unquote(self.headers.get("X-Filename", "app.apk"))
            name = Path(name.replace("\\", "/")).name
            name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)[:120]
            if not name.lower().endswith(".apk"):
                raise ValueError("Selecciona un archivo .apk")
            upload_id = uuid.uuid4().hex
            target = _inside(inbox / f"{upload_id}-{name}", inbox)
            first = b""
            remaining = length
            with target.open("xb") as stream:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Carga interrumpida")
                    if not first:
                        first = chunk[:8]
                    stream.write(chunk)
                    remaining -= len(chunk)
            if not first.startswith(b"PK") or not zipfile.is_zipfile(target):
                target.unlink(missing_ok=True)
                raise ValueError("El archivo no parece un APK/ZIP válido")
            with state["lock"]:
                state["upload_ids"][upload_id] = target
            self._json(201, {"upload_id": upload_id, "filename": name,
                             "size": target.stat().st_size})

        def _start(self) -> None:
            data = self._body_json()
            upload_id = str(data.get("upload_id", ""))
            with state["lock"]:
                apk = state["upload_ids"].get(upload_id)
            if not apk or not apk.is_file():
                raise ValueError("Sube el APK de nuevo para iniciar")
            job_id = state.get("current_job")
            if job_id and state["jobs"].get(job_id, {}).get("status") == "running":
                raise ValueError("Ya hay un proceso en curso")
            project = _safe_slug(str(data.get("project_name") or apk.stem))
            candidate = ws_root / project
            suffix = 2
            while candidate.exists():
                candidate = ws_root / f"{project}-{suffix}"
                suffix += 1
            package_id = str(data.get("package_id") or "").strip()
            if not package_id:
                package_id = "com.example." + re.sub(r"[^a-z0-9]+", ".", project.lower()).strip(".")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package_id):
                raise ValueError("El package ID debe tener formato com.ejemplo.app")
            state["workspace"] = str(candidate.resolve())
            state["apk"] = str(apk)
            state["suggested_name"] = str(data.get("project_name") or project)
            state["suggested_package"] = package_id
            state["analysis_complete"] = False
            state["interview_ready"] = False
            state["plan_ready"] = False
            declare = child_args("declare", str(apk), "--clone", "--out", str(candidate))
            analyze = child_args("analyze", str(apk), "--out", str(candidate))
            def analyzed() -> None:
                state["analysis_complete"] = True
                state["interview_ready"] = True
            result = start_job("Atestación y análisis del APK",
                               [("registrando la declaración", declare),
                                ("analizando APK con REA y herramientas Android", analyze)],
                               on_success=analyzed, cleanup=apk)
            self._json(202, result)

        def _capture(self) -> None:
            data = self._body_json()
            ws = current_ws()
            if state.get("current_job") and state["jobs"].get(state["current_job"], {}).get("status") == "running":
                raise ValueError("Espera a que termine el proceso actual")
            name = _safe_screen(str(data.get("name", "")))
            if not name:
                raise ValueError("Escribe un nombre de pantalla")
            serial = str(data.get("serial") or "").strip()
            if serial and serial not in uicapture.devices():
                raise ValueError("El dispositivo seleccionado ya no está conectado")
            args = ["ui", str(ws), "--screens", name]
            if serial:
                args.extend(["--serial", serial])
            if data.get("vision"):
                args.append("--vision")
            self._json(202, start_job(f"Capturando {name}", [("captura ADB", child_args(*args))]))

        def _plan(self) -> None:
            ws = current_ws()
            if not state.get("analysis_complete") or not (ws / "analysis" / "ANALYSIS.md").is_file():
                raise ValueError("Espera a que termine el análisis")
            if state.get("current_job") and state["jobs"].get(state["current_job"], {}).get("status") == "running":
                raise ValueError("Espera a que termine el proceso actual")
            data = self._body_json()
            allowed = {"app_name", "package_id", "purpose", "improvements", "stack", "screens",
                       "backend", "backend_base_url", "keep_offline", "ui_language",
                       "remove_features", "monetization", "notes"}
            answers = {k: data.get(k) for k in allowed if k in data}
            if not answers.get("app_name") or not answers.get("package_id"):
                raise ValueError("Completa el nombre y package ID propios")
            if answers["stack"] not in ("android-kotlin", "flutter", "react-native", "web-pwa"):
                raise ValueError("Stack no válido")
            if answers.get("backend", "mock") not in ("mock", "same-api", "new-api"):
                raise ValueError("Backend no válido")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z][A-Za-z0-9_]*)+",
                                str(answers["package_id"])):
                raise ValueError("El package ID debe tener formato com.ejemplo.app")
            if not isinstance(answers.get("screens"), list) or any(
                    not isinstance(x, str) for x in answers.get("screens", [])):
                raise ValueError("Las pantallas deben ser una lista de nombres")
            answers["screens"] = [x[:48] for x in answers.get("screens", [])[:30]]
            rea_dir = ws / ".rea"
            rea_dir.mkdir(parents=True, exist_ok=True)
            answer_file = rea_dir / "panel-answers.json"
            answer_file.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
            state["interview_ready"] = False
            def complete() -> None:
                state["interview_ready"] = True
                state["plan_ready"] = True
            cmd = child_args("plan", str(ws), "--answers", str(answer_file), "--structured")
            self._json(202, start_job("Entrevista y propuesta estructurada",
                                      [("preparando CLONE_PLAN", cmd)], on_success=complete))

        def _build(self) -> None:
            ws = current_ws()
            data = self._body_json()
            if data.get("approved") is not True:
                raise ValueError("El plan requiere aprobación explícita")
            if not (ws / "CLONE_PLAN.md").is_file() or not (ws / "answers.json").is_file():
                raise ValueError("Primero completa y revisa la entrevista")
            if (ws / "app").is_dir() and any((ws / "app").iterdir()):
                raise ValueError("El proyecto ya existe; continúa las mejoras en el chat")
            if state.get("current_job") and state["jobs"].get(state["current_job"], {}).get("status") == "running":
                raise ValueError("Espera a que termine el proceso actual")
            args = ["build", str(ws), "--stack", json.loads((ws / "answers.json").read_text(encoding="utf-8"))["stack"], "--verify"]
            self._json(202, start_job("Generación y verificación", [("build + verify", child_args(*args))]))

        def _chat(self) -> None:
            ws = current_ws()
            data = self._body_json()
            message = str(data.get("message", "")).strip()
            if not message or len(message) > 5000:
                raise ValueError("El mensaje debe tener entre 1 y 5000 caracteres")
            if state.get("current_job") and state["jobs"].get(state["current_job"], {}).get("status") == "running":
                raise ValueError("Espera a que termine el trabajo en curso")
            args = ["chat", str(ws), "--message", message]
            self._json(202, start_job("Turno de chat", [("agente y herramientas", child_args(*args))]))

        def _verify(self) -> None:
            ws = current_ws()
            if state.get("current_job") and state["jobs"].get(state["current_job"], {}).get("status") == "running":
                raise ValueError("Espera a que termine el trabajo en curso")
            answers = ws / "answers.json"
            stack = "android-kotlin"
            project = "app/android"
            if answers.is_file():
                data = json.loads(answers.read_text(encoding="utf-8"))
                stack = data.get("stack", stack)
                project = {"android-kotlin": "app/android", "flutter": "app/flutter",
                           "react-native": "app/react-native", "web-pwa": "app/web"}.get(stack, "app")
            self._json(202, start_job("Verificación del proyecto",
                                      [("verify", child_args("verify", str(ws), "--project", project,
                                                              "--stack", stack))]))

        def _status(self) -> None:
            ws = current_ws()
            self._json(202, start_job("Estado del proyecto",
                                      [("status", child_args("status", str(ws)))]))

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    actual_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?token={quote(token)}"
    print("rea-termux · Panel gráfico local")
    print(f"Workspace: {ws_root}")
    print(f"Abre en este teléfono: {url}")
    print("Seguridad: solo loopback; Ctrl-C para cerrar el panel.")
    if open_browser:
        opener = shutil.which("termux-open-url")
        if opener:
            try:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nCerrando panel…")
    finally:
        httpd.server_close()
    return 0
