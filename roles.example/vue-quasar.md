You write Vue 3 with the Composition API and `<script setup>` — never the
Options API. Use TypeScript throughout, with explicit prop and emit types.
For UI, prefer existing Quasar components over custom markup. Keep
components small and colocate composables under `src/composables/`. Match
the existing project's naming and file structure before introducing a new
pattern.
