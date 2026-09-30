$ErrorActionPreference = 'Stop'

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host "Created .env from .env.example. Edit it with your real database/email/dashboard values before starting Docker." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists." -ForegroundColor Green
}

if (-not (Test-Path 'candidate_data')) {
    New-Item -ItemType Directory -Path 'candidate_data' | Out-Null
}

Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. Put your resume at candidate_data\resume.pdf"
Write-Host "  2. Edit .env"
Write-Host "  3. docker compose pull postgres ollama browser"
Write-Host "  4. docker compose build"
Write-Host "  5. docker compose up -d"
