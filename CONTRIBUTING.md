# Handling Merge Conflicts

When rebasing your deploy branch on top of upstream changes, you may encounter merge conflicts. Here's how to handle them effectively:

## Conflict Resolution Process

1. When a conflict occurs during rebase, Git will pause and mark the conflicting files
2. Run `git status` to see which files have conflicts
3. Open each conflicting file in your editor. Look for markers like:
   ```
   <<<<<<< HEAD (Current changes)
   your custom code
   =======
   upstream code
   >>>>>>> upstream/main
   ```

4. For each conflict:
   - Review both versions carefully
   - Decide which parts to keep
   - Remove conflict markers
   - Ensure the final code is correct and maintains your customizations

5. Common conflict patterns and solutions:

   ### Configuration Changes
   ```javascript
   // If upstream changes config structure
   <<<<<<< HEAD
   const config = {
     customEndpoint: process.env.CUSTOM_ENDPOINT,
     timeout: 5000
   };
   =======
   const config = {
     endpoint: process.env.ENDPOINT,
     timeout: 3000,
     newFeature: true
   };
   >>>>>>> upstream/main

   // Merged solution preserving both:
   const config = {
     endpoint: process.env.ENDPOINT,
     customEndpoint: process.env.CUSTOM_ENDPOINT,
     timeout: 5000,  // Keep your custom timeout
     newFeature: true
   };
   ```

   ### Template/UI Changes
   ```html
   <<<<<<< HEAD
   <div class="custom-container">
     <h1>{{ customTitle }}</h1>
     <CustomComponent />
   </div>
   =======
   <div class="container">
     <h1>{{ title }}</h1>
     <NewFeature />
   </div>
   >>>>>>> upstream/main

   <!-- Merged solution incorporating both: -->
   <div class="custom-container">
     <h1>{{ customTitle || title }}</h1>
     <CustomComponent />
     <NewFeature />
   </div>
   ```

6. After resolving each file:
   ```bash
   git add <resolved-file>
   git rebase --continue
   ```

7. Test your changes thoroughly before pushing

## Best Practices

1. Keep custom changes modular and isolated
   - Use separate files when possible
   - Use clear naming conventions for custom features
   - Document why each customization exists

2. Use feature flags
   ```javascript
   if (process.env.CUSTOM_FEATURE) {
     // Your custom logic
   } else {
     // Original logic
   }
   ```

3. Maintain a CHANGELOG.md listing your customizations
   - Helps during conflict resolution
   - Makes it easier to replicate changes if needed

4. Regular syncs help
   - Sync with upstream frequently
   - Smaller, more frequent rebases are easier than big ones

5. When in doubt, create a backup branch
   ```bash
   git checkout deploy
   git checkout -b deploy-backup
   ```