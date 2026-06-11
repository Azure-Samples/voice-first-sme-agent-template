# Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit <https://cla.opensource.microsoft.com>.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Submitting a pull request

- Search [existing issues and pull requests](../../pulls) first so you don't duplicate effort.
- Make your changes in a fork or a feature branch.
- Keep changes focused and include a clear, descriptive commit message.
- Run the tests (and add new ones where it makes sense) before opening the PR.
- Open the pull request against the `main` branch and respond to review feedback.

## Development environment

This template has three parts you may touch when contributing:

```bash
# Backend (FastAPI) — Python 3.11+
pip install -r backend/requirements.txt
python -m pytest                     # backend/ and top-level tests/

# Frontend (React + Vite) — Node.js 18+
cd frontend
npm install
npm run build

# Setup wizard (Node)
cd setup-wizard
npm install
npm test
```

## Code style

Follow the standard conventions of each language already used in the codebase
(Python, TypeScript, Bicep, PowerShell, and Bash). Keep new code consistent with
the surrounding files and avoid unrelated reformatting in the same change.
