name: Fetch EDGAR Filings

on:
  schedule:
    - cron: '0 12 * * 1-5'  # 7am ET Mon-Fri
  workflow_dispatch:          # manual trigger from GitHub UI

jobs:
  fetch:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests

      - name: Fetch filings and send email
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GMAIL_USER: ${{ secrets.GMAIL_USER }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}
        run: python fetch_filings.py

      - name: Commit and push filings.json
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add filings.json
          git diff --staged --quiet || git commit -m "Update filings $(date -u +%Y-%m-%d)"
          git push
