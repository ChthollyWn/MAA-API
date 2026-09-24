import { readFile, writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const webDir = path.resolve(scriptDir, '..')
const schemaPath = path.join(webDir, 'openapi.json')
const outputPath = path.join(webDir, 'src/types/api.d.ts')
const check = process.argv.includes('--check')

const schema = JSON.parse(await readFile(schemaPath, 'utf8'))
const generated = `${astToString(await openapiTS(schema, { readWriteMarkers: true })).trimEnd()}\n`

if (check) {
  let current
  try {
    current = await readFile(outputPath, 'utf8')
  } catch {
    current = ''
  }
  if (current !== generated) {
    console.error('OpenAPI types are stale. Run `pnpm gen:api` and commit api.d.ts with openapi.json.')
    process.exitCode = 1
  } else {
    console.log('OpenAPI types are up to date.')
  }
} else {
  await mkdir(path.dirname(outputPath), { recursive: true })
  await writeFile(outputPath, generated, 'utf8')
  console.log(`Generated ${path.relative(webDir, outputPath)} from ${path.relative(webDir, schemaPath)}.`)
}
