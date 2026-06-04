import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { delimiter, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { findSlashCommand, SLASH_COMMANDS } from '../app/slash/registry.js'

type CommandRoute = 'fallback' | 'local' | 'native'

interface CommandRegistryLoad {
  error?: string
  names: string[]
  python?: string
}

interface PythonCandidate {
  command: string
  label: string
}

interface PythonProbeFailure extends PythonCandidate {
  detail: string
}

const NATIVE_MUTATING_COMMANDS = new Set(['browser', 'busy', 'fast', 'reload-mcp', 'rollback', 'stop'])

const MUTATING_COMMANDS = [
  'background',
  'branch',
  'browser',
  'busy',
  'clear',
  'compress',
  'fast',
  'model',
  'new',
  'personality',
  'queue',
  'reasoning',
  'reload-mcp',
  'retry',
  'rollback',
  'steer',
  'stop',
  'title',
  'tools',
  'undo',
  'verbose',
  'voice',
  'yolo'
] as const

const commandRegistryScript =
  'import json; from hermes_cli.commands import COMMAND_REGISTRY; print(json.dumps([c.name for c in COMMAND_REGISTRY]))'

const existingPathCandidate = (label: string, command: string | undefined): PythonCandidate | null =>
  command && existsSync(command) ? { command, label } : null

const pythonCandidates = (repoRoot: string): PythonCandidate[] => {
  const hermesPython = process.env.HERMES_PYTHON?.trim()
  const python = process.env.PYTHON?.trim()
  const venv = process.env.VIRTUAL_ENV?.trim()
  const fallback = process.platform === 'win32' ? 'python' : 'python3'

  const candidates = [
    hermesPython ? { command: hermesPython, label: 'HERMES_PYTHON' } : null,
    python ? { command: python, label: 'PYTHON' } : null,
    existingPathCandidate('VIRTUAL_ENV/bin/python', venv && resolve(venv, 'bin/python')),
    existingPathCandidate('VIRTUAL_ENV/Scripts/python.exe', venv && resolve(venv, 'Scripts/python.exe')),
    existingPathCandidate('.venv/bin/python', resolve(repoRoot, '.venv/bin/python')),
    existingPathCandidate('.venv/bin/python3', resolve(repoRoot, '.venv/bin/python3')),
    existingPathCandidate('.venv/Scripts/python.exe', resolve(repoRoot, '.venv/Scripts/python.exe')),
    existingPathCandidate('venv/bin/python', resolve(repoRoot, 'venv/bin/python')),
    existingPathCandidate('venv/bin/python3', resolve(repoRoot, 'venv/bin/python3')),
    existingPathCandidate('venv/Scripts/python.exe', resolve(repoRoot, 'venv/Scripts/python.exe')),
    { command: fallback, label: `${fallback} on PATH` }
  ].filter((candidate): candidate is PythonCandidate => Boolean(candidate))

  const seen = new Set<string>()

  return candidates.filter(candidate => {
    const key = candidate.command.toLowerCase()

    if (seen.has(key)) {
      return false
    }

    seen.add(key)

    return true
  })
}

const pythonPathEnv = (repoRoot: string) => {
  const pyPath = process.env.PYTHONPATH?.trim()

  return pyPath ? `${repoRoot}${delimiter}${pyPath}` : repoRoot
}

const probePython = (candidate: PythonCandidate, repoRoot: string): CommandRegistryLoad | PythonProbeFailure => {
  const result = spawnSync(candidate.command, ['-c', commandRegistryScript], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: pythonPathEnv(repoRoot) },
    windowsHide: true
  })

  if (result.error) {
    return { ...candidate, detail: result.error.message }
  }

  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || `exit status ${result.status}`).trim()

    return { ...candidate, detail }
  }

  try {
    const names = JSON.parse(result.stdout) as string[]

    return { names: [...new Set(names)], python: candidate.command }
  } catch (error) {
    return {
      ...candidate,
      detail: `invalid registry JSON from stdout: ${error instanceof Error ? error.message : String(error)}`
    }
  }
}

const loadCommandRegistryNames = (): CommandRegistryLoad => {
  const here = dirname(fileURLToPath(import.meta.url))
  const repoRoot = resolve(here, '../../..')
  const failures: PythonProbeFailure[] = []

  for (const candidate of pythonCandidates(repoRoot)) {
    const result = probePython(candidate, repoRoot)

    if ('names' in result) {
      return result
    }

    failures.push(result)
  }

  return {
    error:
      'Unable to import hermes_cli.commands with any candidate Python. ' +
      'Install the repo Python dependencies, including pyyaml, or set HERMES_PYTHON/PYTHON to a prepared repo venv.\n' +
      failures.map(failure => `- ${failure.label} (${failure.command}): ${failure.detail.split('\n')[0]}`).join('\n'),
    names: []
  }
}

const commandRegistry = loadCommandRegistryNames()

const registryRoutes = () => {
  expect(commandRegistry.error, commandRegistry.error).toBeUndefined()

  return Object.fromEntries(commandRegistry.names.map(name => [name, classifyRoute(name)]))
}

const LOCAL_COMMAND_NAMES = new Set(
  SLASH_COMMANDS.flatMap(command => [command.name, ...(command.aliases ?? [])].map(name => name.toLowerCase()))
)

const classifyRoute = (name: string): CommandRoute => {
  const normalized = name.toLowerCase()

  if (NATIVE_MUTATING_COMMANDS.has(normalized)) {
    return 'native'
  }

  if (LOCAL_COMMAND_NAMES.has(normalized)) {
    return 'local'
  }

  return 'fallback'
}

describe('slash parity matrix', () => {
  it('classifies each command registry command as local/native/fallback', () => {
    const routes = registryRoutes()

    expect(routes['model']).toBe('local')
    expect(routes['browser']).toBe('native')
    expect(routes['reload-mcp']).toBe('native')
    expect(routes['rollback']).toBe('native')
    expect(routes['stop']).toBe('native')
  })

  it('keeps every mutating command off slash-worker fallback', () => {
    const routes = registryRoutes()

    for (const name of MUTATING_COMMANDS) {
      expect(routes[name], `missing command in registry: ${name}`).toBeDefined()
      expect(routes[name], `mutating command must not fallback: ${name}`).not.toBe('fallback')
    }
  })

  it('/q alias resolves to queue, not quit (#31983)', () => {
    // Regression for #31983: the TUI `quit` command used to carry alias `q`,
    // which collided with the Python-side `/queue` alias. TUI-local commands
    // dispatch before the backend, so `/q` resolved to /quit (session.die)
    // instead of queueing a prompt.
    const cmd = findSlashCommand('q')
    expect(cmd, '/q must resolve to a command').toBeDefined()
    expect(cmd!.name).toBe('queue')
  })
})
