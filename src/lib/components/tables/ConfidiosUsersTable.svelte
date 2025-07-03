<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { getUsers } from '$lib/apis/users';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import localizedFormat from 'dayjs/plugin/localizedFormat';

	import Pagination from '$lib/components/common/Pagination.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	dayjs.extend(relativeTime);
	dayjs.extend(localizedFormat);

	const i18n = getContext('i18n');

	let page = 1;
	let users = null;
	let total = null;
	let query = '';
	let orderBy = 'created_at';
	let direction = 'asc';

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

	$: if (page) {
		getUserList();
	}

	$: if (query !== null && orderBy && direction) {
		getUserList();
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
										class="px-2 py-1 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
										on:click={() => handleCreateConfidiosUser(user.id)}
									>
										{$i18n.t('Create User')}
									</button>
								</div>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	<Pagination bind:page count={total} perPage={30} />
{/if}
