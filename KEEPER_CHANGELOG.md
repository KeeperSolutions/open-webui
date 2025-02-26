# KEEPER Customization Changelog

This document tracks all customizations made to our fork of open-webui.

## Branding Changes
- Updated logos throughout the application to KEEPER branding
- Modified title text to reflect KEEPER company name
- Locations of branding changes:
  - Main application header
  - Login page
  - Favicon
  - Documentation references

## Infrastructure Changes
- Modified Dockerfile for our deployment requirements
- Added GitHub Actions workflows:
  - `cloud-run-deploy.yml` for continuous deployment to Google Cloud Run
  - `upstream-sync.yml` for automated synchronization with upstream repository

## File Changes
The following files have been modified from the original repository:

### UI/Branding
- `src/app/layout.tsx` - Updated title and metadata
- `backend/open_webui/static/favicon.png` - Replaced with KEEPER favicon
- `backend/open_webui/static/logo.png` - Updated to KEEPER logo

### Infrastructure
- `Dockerfile` - Customized for our deployment needs
- `.github/workflows/cloud-run-deploy.yml` - Added for Cloud Run deployment
- `.github/workflows/upstream-sync.yml` - Added for upstream syncing
- `KEEPER_CHANGELOG.md` - Added to track our changes

## Version Control
- Maintaining `deploy` branch for our customizations
- `main` branch syncs with upstream repository
- Automated daily sync attempts with upstream

## Future Considerations
- Document any new customizations in this file
- Keep track of which files are modified to help with conflict resolution
- Maintain clear separation between core functionality and KEEPER-specific changes

## Workflow
1. All customizations are made in the `deploy` branch
2. Upstream changes are automatically synced to `main` branch
3. Changes are automatically deployed to Cloud Run when `deploy` branch is updated