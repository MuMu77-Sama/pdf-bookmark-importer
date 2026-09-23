# Locate a usable Python 3 interpreter and describe it as `set "VAR=value"`
# lines that the .cmd launchers can `for /f` over.
#
# Two things make this more than a "is python on PATH" check:
#   * a Windows Store alias stub named python.exe exists on many machines and
#     must not be mistaken for a real interpreter;
#   * a Python installation can be split across two directories (the
#     interpreter in one, the standard library in another), which is repaired
#     here by pairing them through PYTHONHOME.
#
# Deliberately written against Windows PowerShell 5.1 syntax.

$ErrorActionPreference = "SilentlyContinue"

function Test-Interpreter {
    # NOTE: the parameter must not be called $Home -- PowerShell variable names
    # are case-insensitive and $HOME is a read-only automatic variable, so
    # binding would fail silently and leave PYTHONHOME pointing at the user
    # profile instead of the standard library.
    param(
        [string]$Exe,
        [string]$StdlibHome,
        [string[]]$Modules = @("zlib", "argparse")
    )

    if (-not $Exe) { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }

    if ($StdlibHome) {
        $env:PYTHONHOME = $StdlibHome
        $env:PYTHONPATH = Join-Path $StdlibHome "Lib"
    } else {
        $env:PYTHONHOME = $null
        $env:PYTHONPATH = $null
    }
    $env:PYTHONIOENCODING = "utf-8"

    # argparse (not zlib) is the meaningful probe: zlib is a built-in C module
    # and imports fine even from an interpreter whose standard library is
    # missing entirely, which is exactly the broken case we must exclude.
    $code = "import " + ($Modules -join ", ")
    & $Exe -c $code 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-CandidateExes {
    $exes = New-Object System.Collections.Generic.List[string]
    $bases = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "C:\",
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )
    foreach ($base in $bases) {
        if (-not $base) { continue }
        $pattern = Join-Path $base "Python3*"
        Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $candidate = Join-Path $_.FullName "python.exe"
            if (Test-Path -LiteralPath $candidate) { $exes.Add($candidate) }
        }
    }
    return $exes
}

function Get-StdlibRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    $bases = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "C:\",
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )
    foreach ($base in $bases) {
        if (-not $base) { continue }
        $pattern = Join-Path $base "Python3*"
        Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            if (Test-Path -LiteralPath (Join-Path $_.FullName "Lib\os.py")) {
                $roots.Add($_.FullName)
            }
        }
    }
    return $roots
}

$foundExe = $null
$foundHome = $null

# The GUI additionally needs tkinter, so it must be probed for as well.
$required = @("zlib", "argparse")
if ($args.Count -ge 1 -and $args[0] -eq "gui") {
    $required += "tkinter"
}

# 1) An explicit override always wins.
if ($env:PDFBOOKMARKS_PYTHON) {
    $override = $env:PDFBOOKMARKS_PYTHON
    if (Test-Interpreter -Exe $override -Modules $required) {
        $foundExe = $override
    } elseif (Test-Interpreter -Exe $override -StdlibHome (Split-Path $override -Parent) -Modules $required) {
        $foundExe = $override
        $foundHome = Split-Path $override -Parent
    }
}

# 2) Anything already on PATH.
if (-not $foundExe) {
    foreach ($name in @("python", "python3")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            if (Test-Interpreter -Exe $command.Source -Modules $required) {
                $foundExe = $command.Source
                break
            }
        }
    }
}

# 3) Scan the usual installation roots, pairing an interpreter with any
#    standard library we can find when the two are not in one directory.
#
#    The order matters: a pairing that sets PYTHONHOME correctly also makes
#    sys.prefix correct and silences Python's "Could not find platform
#    independent libraries" warning, so it is preferred over a bare
#    interpreter that only works because of a registry PythonPath entry.
if (-not $foundExe) {
    $stdlibRoots = @(Get-StdlibRoots)
    foreach ($exe in @(Get-CandidateExes)) {
        $own = Split-Path $exe -Parent
        if (Test-Interpreter -Exe $exe -StdlibHome $own -Modules $required) {
            $foundExe = $exe
            $foundHome = $own
            break
        }
        foreach ($root in $stdlibRoots) {
            if (Test-Interpreter -Exe $exe -StdlibHome $root -Modules $required) {
                $foundExe = $exe
                $foundHome = $root
                break
            }
        }
        if ($foundExe) { break }
        if (Test-Interpreter -Exe $exe -Modules $required) {
            $foundExe = $exe
            break
        }
    }
}

if ($foundExe) {
    'set "PYEXE=' + $foundExe + '"'
    if ($foundHome) { 'set "PYHOME=' + $foundHome + '"' }
    'set "PYEXE_OK=1"'
} else {
    'set "PYEXE_OK="'
}
