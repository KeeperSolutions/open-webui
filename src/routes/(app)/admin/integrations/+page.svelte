<script lang="ts">
    import { getContext } from 'svelte';
    import { toast } from 'svelte-sonner';
    import { apiSpec } from '$lib/stores/integrations';
    import { WEBUI_API_BASE_URL } from '$lib/constants';

    const i18n = getContext('i18n');

    interface ApiMethod {
        method: string;
        summary: string;
        tags: string[];
        operationId: string;
    }

    interface ApiPath {
        path: string;
        methods: ApiMethod[];
    }

    interface FormattedSpec {
        title: string;
        version: string;
        description: string;
        paths: ApiPath[];
    }

    let isLoading = false;

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
            if (data.api_spec) {
                $apiSpec = data.api_spec;
            }
        } catch (error) {
            toast.error($i18n.t('Failed to login to Confidios. Please try again.'));
        } finally {
            isLoading = false;
        }
    }

    function handleClearSpec() {
        $apiSpec = null;
    }

    function formatApiSpec(spec: any): FormattedSpec {
        if (!spec) return {
            title: '',
            version: '',
            description: '',
            paths: []
        };

        const parsedSpec = typeof spec === 'string' ? JSON.parse(spec) : spec;

        return {
            title: parsedSpec.info?.title || 'API Specification',
            version: parsedSpec.info?.version || '',
            description: parsedSpec.info?.description || '',
            paths: Object.entries(parsedSpec.paths || {}).map(([path, methods]: [string, any]) => ({
                path,
                methods: Object.entries(methods).map(([method, details]: [string, any]) => ({
                    method: method.toUpperCase(),
                    summary: details.summary || details.description || '',
                    tags: details.tags || [],
                    operationId: details.operationId || ''
                }))
            }))
        };
    }

    $: formatted = formatApiSpec($apiSpec);
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
                <span class="text-sm text-gray-400">Not logged in</span>
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

    {#if $apiSpec}
        <div class="mt-4 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-semibold">API Specification</h2>
                <button
                    class="px-3 py-1 text-sm bg-red-500 text-white rounded hover:bg-red-600"
                    on:click={handleClearSpec}
                >
                    Clear
                </button>
            </div>

            <div class="space-y-4">
                <div class="mb-6">
                    <h3 class="text-lg font-semibold">{formatted.title} v{formatted.version}</h3>
                    <p class="text-gray-600 dark:text-gray-400">{formatted.description}</p>
                </div>

                <div class="space-y-4">
                    {#each formatted.paths as { path, methods }}
                        <div class="border-t pt-4 first:border-t-0 first:pt-0">
                            <h4 class="font-mono text-md font-semibold mb-2">{path}</h4>
                            <div class="space-y-2">
                                {#each methods as { method, summary, tags, operationId }}
                                    <div class="flex gap-2 items-start">
                                        <span class="px-2 py-1 text-xs font-semibold rounded
                                            {method === 'GET' ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-100' : ''}
                                            {method === 'POST' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-100' : ''}
                                            {method === 'PUT' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-100' : ''}
                                            {method === 'DELETE' ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-100' : ''}">
                                            {method}
                                        </span>
                                        <div class="flex-1">
                                            <p class="text-sm">{summary || operationId}</p>
                                            {#if tags.length > 0}
                                                <div class="flex gap-1 mt-1">
                                                    {#each tags as tag}
                                                        <span class="px-2 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700">
                                                            {tag}
                                                        </span>
                                                    {/each}
                                                </div>
                                            {/if}
                                        </div>
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        </div>
    {/if}
</div>