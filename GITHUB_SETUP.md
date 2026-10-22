# Acceso seguro a GitHub para este proyecto

No guardes un Personal Access Token (PAT) en un archivo del proyecto ni lo incluyas en Git. Un archivo ignorado sigue siendo texto plano y puede acabar en copias, registros o una subida accidental.

## Token y alcance acordado

El propietario decidió mantener activo el token compartido hasta terminar el desarrollo. Se utilizará exclusivamente para crear y actualizar este repositorio, no se guardará en el proyecto y no se imprimirá en logs. Al concluir el desarrollo, revócalo. Para futuras credenciales, se recomienda un PAT **fine-grained** limitado a este repositorio y con permisos mínimos.

## Usar el token nuevo solo durante una operación

Cuando el repositorio remoto ya esté configurado, introduce el token de forma interactiva en una terminal (no lo pegues en un comando ni lo guardes en el historial):

```bash
read -rsp 'Token GitHub: ' GH_TOKEN; echo
export GH_TOKEN
# Ejecuta aquí la operación Git necesaria para este repositorio.
unset GH_TOKEN
```

Mantén `GH_TOKEN` únicamente durante la operación y no lo imprimas ni lo registres. Si se necesita automatizar el acceso, usa un credential helper temporal y limitado al repositorio; no añadas el secreto a `rea-termux.toml`, `.env`, scripts, archivos de configuración o commits.

## Estado actual

Esta carpeta todavía no tiene un repositorio Git inicializado ni un remoto `origin` configurado. Para publicar el proyecto, primero habrá que crear el repositorio y configurar su URL remota; después se podrá usar una credencial nueva y de alcance mínimo.
