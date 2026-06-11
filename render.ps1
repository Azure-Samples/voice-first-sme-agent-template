$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
python "$scriptDir/scripts/render.py" @args
