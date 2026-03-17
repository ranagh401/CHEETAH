# CHEETAH

LangGraph-powered Streamlit app to manage Azure DevOps work items assigned to you.

## Features

- Chat with `CHEETAH` to manage Azure DevOps without manual forms
- Use a LangGraph agent workflow with chat history, execution state, and retry logic
- Load user stories assigned to you and select one from a dropdown as the working context
- Show child tasks under the selected user story
- Create multiple tasks under the selected user story from a natural-language instruction
- Update task title, assignee, state, hours, and start/finish dates
- Update all tasks under the selected story in one command
- Add a comment to an existing work item
- Use Azure OpenAI plus LangGraph to plan, execute, inspect errors, and retry
- Fall back to a simpler planner if Azure OpenAI is not configured

## Setup

1. Create a virtual environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your secrets.

Required Azure DevOps variables:

```env
AZDO_ORG_URL=https://dev.azure.com/polestar-products
AZDO_PROJECT=nextgen_platform
AZDO_PAT=your-pat
AZDO_USER_EMAIL=pratham.rana@polestaranalytics.com
```

Optional Azure OpenAI variables:

```env
AZURE_OPENAI_ENDPOINT=https://pssl-genai-2.openai.azure.com/
AZURE_OPENAI_API_KEY=your-rotated-key
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_DEPLOYMENT=pssl-gpt-4o
```

## Run

```powershell
streamlit run app.py
```

## Deploy

Recommended options:

- `Azure App Service` if you want this near your Azure setup and accessible from phone/browser
- `Streamlit Community Cloud` for a quick demo if the repo can be hosted where Streamlit can pull it
- `Render` or `Railway` if you want a simple public/private web deployment with a startup command

### Streamlit Community Cloud

For Streamlit Cloud, do not use `.env` in production. Use managed secrets instead.

1. Push this project to GitHub.
2. In Streamlit Community Cloud, create a new app from the repo.
3. Set the main file path to `app.py`.
4. In the app settings, open `Secrets`.
5. Copy values from [.streamlit/secrets.toml.example](c:/Users/pratham.rana/OneDrive%20-%20Polestar%20Solutions%20%26%20Services%20India%20Private%20Limited/Desktop/CHEETAH%20SERVICE/.streamlit/secrets.toml.example) and paste your real values there.

Managed secrets example:

```toml
AZDO_ORG_URL = "https://dev.azure.com/polestar-products"
AZDO_PROJECT = "nextgen_platform"
AZDO_PAT = "your-real-pat"
AZDO_USER_EMAIL = "your-email@company.com"

AZURE_OPENAI_ENDPOINT = "https://your-endpoint.openai.azure.com/"
AZURE_OPENAI_API_KEY = "your-real-key"
AZURE_OPENAI_API_VERSION = "2024-12-01-preview"
AZURE_OPENAI_DEPLOYMENT = "your-deployment"
```

Generic startup command:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT
```

Environment variables needed in deployment:

```env
AZDO_ORG_URL=...
AZDO_PROJECT=...
AZDO_PAT=...
AZDO_USER_EMAIL=...
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=...
AZURE_OPENAI_DEPLOYMENT=...
```

## Example prompts

```text
Show tasks in this story
Create 5 tasks for API regression testing
Move all tasks in this story to Active
Set remaining work to 2 hours for task 67890
Set actual date to 2026-03-25 for every task in this story
Add comment to 67890: blocked on backend data
```

## PAT scopes

Recommended minimum scopes for the Azure DevOps PAT:

- Work Items: Read & Write
- Project and Team: Read

If your Azure DevOps process uses custom states or custom fields, adjust the defaults and planner rules in [app.py](c:/Users/pratham.rana/OneDrive%20-%20Polestar%20Solutions%20%26%20Services%20India%20Private%20Limited/Desktop/CHEETAH%20SERVICE/app.py).
