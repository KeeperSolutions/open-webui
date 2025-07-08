<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { user } from '$lib/stores';
	import { getUsers } from '$lib/apis/users';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import localizedFormat from 'dayjs/plugin/localizedFormat';

	import Pagination from '$lib/components/common/Pagination.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import { confidiosStore } from '$lib/stores/integrations/';

	dayjs.extend(relativeTime);
	dayjs.extend(localizedFormat);

	const i18n = getContext('i18n');

	interface User {
		id: string;
		name: string;
		email: string;
		role: string;
		profile_image_url: string;
		confidios_balance?: string; // Add optional balance field
	}

	let page = 1;
	let users: User[] | null = null;
	let total: number | null = null;
	let query = '';
	let orderBy = 'created_at';
	let direction = 'asc';

	// Add the reactive declarations here
	$: currentUser = $user;
	$: isCurrentUser = (userId: string) => currentUser?.id === userId;
	$: isAdminLoggedIn = $confidiosStore.isAdminLoggedIn;

	const setSortKey = (key) => {
		if (orderBy === key) {
			direction = direction === 'asc' ? 'desc' : 'asc';
		} else {
			orderBy = key;
			direction = 'asc';
		}
	};

	const getUserList = async () => {
		try {
			const res = await getUsers(localStorage.token, query, orderBy, direction, page).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (res) {
				users = res.users;
				total = res.total;
			}
		} catch (err) {
			console.error(err);
		}
	};
	// Reactively fetch user list when page, query, orderBy, or direction changes
	$: getUserList();

	let loadingStates: { [key: string]: boolean } = {};

	async function handleCreateConfidiosUser(user: User) {
		try {
			loadingStates[user.id] = true;

			const response = await fetch(`${WEBUI_API_BASE_URL}/confidios/users/create`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				},
				body: JSON.stringify({
					user_id: user.id,
					name: user.name,
					email: user.email,
					role: user.role,
					profile_image_url: user.profile_image_url
				})
			});

			const data = await response.json();

			if (!response.ok) {
				throw new Error(data.detail || 'Failed to create user');
			}

			if (data.confidios_user?.balance) {
				user.confidios_balance = data.confidios_user.balance;
			}

			console.log('Response:', data);
			toast.success($i18n.t('User created successfully'));
			if (users) {
				users = [...users];
			}
		} catch (error) {
			console.error('Error:', error);
			toast.error(error.message);
		} finally {
			loadingStates[user.id] = false;
		}
	}
</script>

{#if users === null || total === null}
	<div class="my-10">
		<Spinner />
	</div>
{:else}
	<div class="mt-0.5 mb-2 gap-1 flex flex-col md:flex-row justify-between">
		<div class="flex md:self-center text-lg font-medium px-0.5">
			<div class="flex-shrink-0">
				{$i18n.t('Users')}
			</div>
			<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
			<span class="text-lg font-medium text-gray-500 dark:text-gray-300">{total}</span>
		</div>

		<div class="flex gap-1">
			<div class="flex w-full space-x-2">
				<div class="flex flex-1">
					<div class="self-center ml-1 mr-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 20 20"
							fill="currentColor"
							class="w-4 h-4"
						>
							<path
								fill-rule="evenodd"
								d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
					<input
						class="w-full text-sm pr-4 py-1 rounded-xl outline-hidden bg-transparent"
						bind:value={query}
						placeholder={$i18n.t('Search')}
					/>
				</div>
			</div>
		</div>
	</div>

	<div class="scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm">
		<table
			class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto max-w-full rounded-sm"
		>
			<thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-850 dark:text-gray-400">
				<tr>
					<th
						scope="col"
						class="px-3 py-1.5 cursor-pointer select-none"
						on:click={() => setSortKey('role')}
					>
						<div class="flex gap-1.5 items-center">
							{$i18n.t('Role')}
							{#if orderBy === 'role'}
								<span class="font-normal">
									{#if direction === 'asc'}
										<ChevronUp className="size-2" />
									{:else}
										<ChevronDown className="size-2" />
									{/if}
								</span>
							{:else}
								<span class="invisible">
									<ChevronUp className="size-2" />
								</span>
							{/if}
						</div>
					</th>
					<th
						scope="col"
						class="px-3 py-1.5 cursor-pointer select-none"
						on:click={() => setSortKey('name')}
					>
						<div class="flex gap-1.5 items-center">
							{$i18n.t('Name')}
							{#if orderBy === 'name'}
								<span class="font-normal">
									{#if direction === 'asc'}
										<ChevronUp className="size-2" />
									{:else}
										<ChevronDown className="size-2" />
									{/if}
								</span>
							{:else}
								<span class="invisible">
									<ChevronUp className="size-2" />
								</span>
							{/if}
						</div>
					</th>
					<th
						scope="col"
						class="px-3 py-1.5 cursor-pointer select-none"
						on:click={() => setSortKey('email')}
					>
						<div class="flex gap-1.5 items-center">
							{$i18n.t('Email')}
							{#if orderBy === 'email'}
								<span class="font-normal">
									{#if direction === 'asc'}
										<ChevronUp className="size-2" />
									{:else}
										<ChevronDown className="size-2" />
									{/if}
								</span>
							{:else}
								<span class="invisible">
									<ChevronUp className="size-2" />
								</span>
							{/if}
						</div>
					</th>
					<th scope="col" class="px-3 py-1.5">
						<div class="flex gap-1.5 items-center justify-end">
							{$i18n.t('Confidios Status')}
						</div>
					</th>
					<th scope="col" class="px-3 py-1.5">
						<div class="flex gap-1.5 items-center justify-end">
							{$i18n.t('Balance')}
						</div>
					</th>
				</tr>
			</thead>
			<tbody>
				{#each users as user}
					<tr class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs">
						<td class="px-3 py-1 min-w-[7rem] w-28">
							<Badge
								type={user.role === 'admin' ? 'info' : user.role === 'user' ? 'success' : 'muted'}
								content={$i18n.t(user.role)}
							/>
						</td>
						<td class="px-3 py-1 font-medium text-gray-900 dark:text-white w-max">
							<div class="flex flex-row w-max">
								<img
									class="rounded-full w-6 h-6 object-cover mr-2.5"
									src={user.profile_image_url.startsWith(WEBUI_BASE_URL) ||
									user.profile_image_url.startsWith('https://www.gravatar.com/avatar/') ||
									user.profile_image_url.startsWith('data:')
										? user.profile_image_url
										: `/user.png`}
									alt="user"
								/>
								<div class="font-medium self-center">{user.name}</div>
							</div>
						</td>
						<td class="px-3 py-1">{user.email}</td>
						<td class="px-3 py-1">
							{#if user.confidios_user_id}
								<Badge type="success" content={$i18n.t('Active')} />
							{:else}
								<div class="flex justify-end items-center">
									<button
										class={`px-2 py-1 text-xs font-medium text-white rounded hover:opacity-90 flex items-center gap-2 min-w-[90px] justify-center disabled:opacity-50 ${
											user.confidios_balance
												? 'bg-green-600 hover:bg-green-700 dark:bg-green-500 dark:hover:bg-green-600'
												: 'bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600'
										}`}
										on:click={() => handleCreateConfidiosUser(user)}
										disabled={loadingStates[user.id] || isCurrentUser(user.id) || !isAdminLoggedIn}
										title={isCurrentUser(user.id)
											? $i18n.t('Cannot create Confidios account for yourself')
											: !isAdminLoggedIn
												? $i18n.t('Please login to Confidios first')
												: ''}
									>
										{#if loadingStates[user.id]}
											<svg
												class="animate-spin h-4 w-4"
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
											>
												<circle
													class="opacity-25"
													cx="12"
													cy="12"
													r="10"
													stroke="currentColor"
													stroke-width="4"
												></circle>
												<path
													class="opacity-75"
													fill="currentColor"
													d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
												></path>
											</svg>
										{/if}
										{#if isCurrentUser(user.id)}
											{$i18n.t('Cannot create for self')}
										{:else if user.confidios_balance}
											{$i18n.t('User created')}
										{:else}
											{loadingStates[user.id] ? $i18n.t('Creating...') : $i18n.t('Create User')}
										{/if}
									</button>
								</div>
							{/if}
						</td>
						<td class="px-3 py-1 text-right">
							{#if user.confidios_balance}
								<span class="font-medium">
									{Number(user.confidios_balance).toFixed(8)}
								</span>
							{:else}
								<span class="text-gray-400">-</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	<Pagination bind:page count={total} perPage={30} />
{/if}
