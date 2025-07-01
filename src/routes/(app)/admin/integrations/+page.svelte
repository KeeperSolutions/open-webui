

<script lang="ts">
    import { getContext } from 'svelte';
    import { toast } from 'svelte-sonner';
    const i18n = getContext('i18n');
    import { WEBUI_API_BASE_URL } from '$lib/constants';
    async function handleConfidiosLogin() {
        try {
            const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/auths/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.token}`
                }
            });

            if (!response.ok) {
                throw new Error('Failed to login');
            }

            const data = await response.json();
            toast.success(data.status);
        } catch (error) {
            toast.error($i18n.t('Failed to login to Confidios. Please try again.'));
        }
    }
</script>

<div class="flex flex-col gap-4">
    <div class="flex items-center justify-between">
        <h1 class="text-2xl font-bold">
            {$i18n.t('Integrations')}
        </h1>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <!-- Confidios Integration Card -->
        <div class="p-4 rounded-lg border border-gray-200 dark:border-gray-700">
            <h2 class="text-xl font-semibold mb-2">Confidios</h2>
            <p class="text-gray-600 dark:text-gray-400 mb-4">
                Secure feature management and access control
            </p>
            <div class="flex items-center justify-between">
                <span class="text-sm text-gray-400">Not logged in</span>
                <button
                    class="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
                    on:click={handleConfidiosLogin}
                >
                    Admin Login
                </button>
            </div>
        </div>


    </div>
</div>