<script lang="ts">
    import { getContext } from 'svelte';
    import { toast } from 'svelte-sonner';
    import { WEBUI_API_BASE_URL } from '$lib/constants';

    const i18n = getContext('i18n');
    let isLoading = false;
    let isLoggedIn = false;

    async function handleConfidiosLogin() {
        try {
            isLoading = true;
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
            isLoggedIn = data.confidios_is_logged_in;
        } catch (error) {
            toast.error($i18n.t('Failed to login to Confidios. Please try again.'));
        } finally {
            isLoading = false;
        }
    }
</script>

<style>
    .spinner {
        animation: spin 1s linear infinite;
    }

    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
</style>

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
                <div class="flex items-center gap-2">
                    {#if isLoggedIn}
                        <svg class="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                            <path d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"/>
                        </svg>
                        <span class="text-sm text-green-600 dark:text-green-400">Logged in</span>
                    {:else}
                        <span class="text-sm text-gray-400">Not logged in</span>
                    {/if}
                </div>
                <button
                    class="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 flex items-center gap-2"
                    on:click={handleConfidiosLogin}
                    disabled={isLoading}
                >
                    {#if isLoading}
                        <svg
                            class="spinner w-4 h-4"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            stroke-width="2"
                        >
                            <circle
                                class="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                fill="none"
                            ></circle>
                            <path
                                class="opacity-75"
                                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                                fill="currentColor"
                            ></path>
                        </svg>
                    {/if}
                    {isLoading ? 'Loading...' : 'Admin Login'}
                </button>
            </div>
        </div>
    </div>
</div>