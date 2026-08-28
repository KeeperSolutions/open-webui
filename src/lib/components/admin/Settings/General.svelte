<script lang="ts">
	import DOMPurify from 'dompurify';
	import { v4 as uuidv4 } from 'uuid';

	import { getBackendConfig, getVersionUpdates } from '$lib/apis';
	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import { getBanners, setBanners } from '$lib/apis/configs';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { WEBUI_BUILD_HASH, WEBUI_VERSION } from '$lib/constants';
	import { banners as _banners, config, showChangelog } from '$lib/stores';
	import type { Banner } from '$lib/types';
	import { compareVersion } from '$lib/utils';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Banners from './Interface/Banners.svelte';
	import Events from './Events.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';

	const i18n: any = getContext('i18n');

	export let saveHandler: Function;

	let updateAvailable: boolean | null = false;
	let version = {
		current: WEBUI_VERSION,
		latest: WEBUI_VERSION
	};

	let adminConfig: any = null;

	let banners: Banner[] = [];
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const checkForVersionUpdates = async () => {
		updateAvailable = null;
		version = await getVersionUpdates(localStorage.token).catch((error) => {
			return {
				current: WEBUI_VERSION,
				latest: WEBUI_VERSION
			};
		});

		console.info(version);

		updateAvailable = compareVersion(version.latest, version.current);
		console.info(updateAvailable);
	};

	const updateBanners = async () => {
		_banners.set(await setBanners(localStorage.token, banners));
	};

	const updateHandler = async () => {
		const res = await updateAdminConfig(localStorage.token, adminConfig);

		await updateBanners();

		await config.set(await getBackendConfig());

		if (res) {
			saveHandler();
		} else {
			toast.error($i18n.t('Failed to update settings'));
		}
	};

	onMount(async () => {
		adminConfig = await getAdminConfig(localStorage.token);

		banners = [...$_banners];
	});
</script>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={async () => {
		updateHandler();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$i18n.t('General')}</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if adminConfig !== null}
			<AdminSettingSection first>
				<div class="flex items-start justify-between gap-4">
					<div class="min-w-0 text-xs">
						<div class="text-gray-600 dark:text-gray-400">{$i18n.t('Version')}</div>
						<div class="mt-1 flex flex-wrap gap-x-1 text-gray-700 dark:text-gray-200">
							<Tooltip content={WEBUI_BUILD_HASH}>v{WEBUI_VERSION}</Tooltip>

							{#if $config?.features?.enable_version_update_check}
								<a
									href="https://github.com/open-webui/open-webui/releases/tag/v{version.latest}"
									target="_blank"
									class="text-gray-500 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
								>
									{updateAvailable === null
										? $i18n.t('Checking for updates...')
										: updateAvailable
											? `(v${version.latest} ${$i18n.t('available!')})`
											: $i18n.t('(latest)')}
								</a>
							{/if}
						</div>

						<button
							class="mt-0.5 text-xs text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							type="button"
							on:click={() => {
								showChangelog.set(true);
							}}
						>
							{$i18n.t("See what's new")}
						</button>
					</div>

					{#if $config?.features?.enable_version_update_check}
						<button
							class="shrink-0 text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
							type="button"
							on:click={() => {
								checkForVersionUpdates();
							}}
						>
							{$i18n.t('Check for updates')}
						</button>
					{/if}
				</div>

				<div class="text-xs">
					<div class="flex items-start justify-between gap-4">
						<div class="min-w-0">
							<div class="text-gray-600 dark:text-gray-400">{$i18n.t('Help')}</div>
							<div class="mt-0.5 text-gray-400 dark:text-gray-600">
								{$i18n.t('Discover how to use Open WebUI and seek support from the community.')}
							</div>
						</div>

						<a
							class="shrink-0 text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
							href="https://docs.openwebui.com/"
							target="_blank"
						>
							{$i18n.t('Documentation')}
						</a>
					</div>

					<div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-gray-400 dark:text-gray-600">
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://discord.gg/5rJgQTnV4s"
							target="_blank">Discord</a
						>
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://twitter.com/OpenWebUI"
							target="_blank">X</a
						>
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://github.com/open-webui/open-webui"
							target="_blank">GitHub</a
						>
					</div>
				</div>

				<div class="text-xs">
					<div class="text-gray-600 dark:text-gray-400">{$i18n.t('License')}</div>

					{#if $config?.license_metadata}
						<a
							href="https://docs.openwebui.com/enterprise"
							target="_blank"
							class="mt-0.5 block text-gray-500"
						>
							<span class="capitalize text-black dark:text-white"
								>{$config?.license_metadata?.type} license</span
							>
							registered to
							<span class="capitalize text-black dark:text-white"
								>{$config?.license_metadata?.organization_name}</span
							>
							for
							<span class="text-black dark:text-white"
								>{$config?.license_metadata?.seats ?? 'Unlimited'} users.</span
							>
						</a>
						{#if $config?.license_metadata?.html}
							<div class="mt-0.5 text-gray-500">
								{@html DOMPurify.sanitize($config?.license_metadata?.html)}
							</div>
						{/if}
<<<<<<< HEAD
					{/if}

					<div class=" mb-2.5 w-full justify-between">
						<div class="flex w-full justify-between">
							<div class=" self-center text-xs font-medium">{$i18n.t('JWT Expiration')}</div>
						</div>

						<div class="flex mt-2 space-x-2">
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								type="text"
								placeholder={`e.g.) "30m","1h", "10d". `}
								bind:value={adminConfig.JWT_EXPIRES_IN}
							/>
						</div>

						<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Valid time units:')}
							<span class=" text-gray-300 font-medium"
								>{$i18n.t("'s', 'm', 'h', 'd', 'w' or '-1' for no expiration.")}</span
							>
						</div>

						{#if adminConfig.JWT_EXPIRES_IN === '-1'}
							<div class="mt-2 text-xs">
								<div
									class=" bg-yellow-500/20 text-yellow-700 dark:text-yellow-200 rounded-lg px-3 py-2"
								>
									<div>
										<span class=" font-medium">{$i18n.t('Warning')}:</span>
										<span
											><a
												href="https://docs.openwebui.com/reference/env-configuration#jwt_expires_in"
												target="_blank"
												class=" underline"
												>{$i18n.t('No expiration can pose security risks.')}
											</a></span
										>
									</div>
								</div>
							</div>
						{/if}
					</div>

					<div class=" space-y-3">
						<div class="mt-2 space-y-2 pr-1.5">
							<div class="flex justify-between items-center text-sm">
								<div class="  font-medium">{$i18n.t('LDAP')}</div>

								<div class="mt-1">
									<Switch bind:state={ENABLE_LDAP} />
								</div>
							</div>

							{#if ENABLE_LDAP}
								<div class="flex flex-col gap-1">
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Label')}
											</div>
											<input
												class="w-full bg-transparent outline-hidden py-0.5"
												required
												placeholder={$i18n.t('Enter server label')}
												bind:value={LDAP_SERVER.label}
											/>
										</div>
										<div class="w-full"></div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Host')}
											</div>
											<input
												class="w-full bg-transparent outline-hidden py-0.5"
												required
												placeholder={$i18n.t('Enter server host')}
												bind:value={LDAP_SERVER.host}
											/>
										</div>
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Port')}
											</div>
											<Tooltip
												placement="top-start"
												content={$i18n.t('Default to 389 or 636 if TLS is enabled')}
												className="w-full"
											>
												<input
													class="w-full bg-transparent outline-hidden py-0.5"
													type="number"
													placeholder={$i18n.t('Enter server port')}
													bind:value={LDAP_SERVER.port}
												/>
											</Tooltip>
										</div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Application DN')}
											</div>
											<Tooltip
												content={$i18n.t('The Application Account DN you bind with for search')}
												placement="top-start"
											>
												<input
													class="w-full bg-transparent outline-hidden py-0.5"
													placeholder={$i18n.t('Enter Application DN')}
													bind:value={LDAP_SERVER.app_dn}
												/>
											</Tooltip>
										</div>
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Application DN Password')}
											</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Application DN Password')}
												required={false}
												bind:value={LDAP_SERVER.app_dn_password}
											/>
										</div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Attribute for Mail')}
											</div>
											<Tooltip
												content={$i18n.t(
													'The LDAP attribute that maps to the mail that users use to sign in.'
												)}
												placement="top-start"
											>
												<input
													class="w-full bg-transparent outline-hidden py-0.5"
													required
													placeholder={$i18n.t('Example: mail')}
													bind:value={LDAP_SERVER.attribute_for_mail}
												/>
											</Tooltip>
										</div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Attribute for Username')}
											</div>
											<Tooltip
												content={$i18n.t(
													'The LDAP attribute that maps to the username that users use to sign in.'
												)}
												placement="top-start"
											>
												<input
													class="w-full bg-transparent outline-hidden py-0.5"
													required
													placeholder={$i18n.t(
														'Example: sAMAccountName or uid or userPrincipalName'
													)}
													bind:value={LDAP_SERVER.attribute_for_username}
												/>
											</Tooltip>
										</div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Search Base')}
											</div>
											<Tooltip
												content={$i18n.t('The base to search for users')}
												placement="top-start"
											>
												<input
													class="w-full bg-transparent outline-hidden py-0.5"
													required
													placeholder={$i18n.t('Example: ou=users,dc=foo,dc=example')}
													bind:value={LDAP_SERVER.search_base}
												/>
											</Tooltip>
										</div>
									</div>
									<div class="flex w-full gap-2">
										<div class="w-full">
											<div class=" self-center text-xs font-medium min-w-fit mb-1">
												{$i18n.t('Search Filters')}
											</div>
											<input
												class="w-full bg-transparent outline-hidden py-0.5"
												placeholder={$i18n.t('Example: (&(objectClass=inetOrgPerson)(uid=%s))')}
												bind:value={LDAP_SERVER.search_filters}
											/>
										</div>
									</div>
									<div class="text-xs text-gray-400 dark:text-gray-500">
										<a
											class=" text-gray-300 font-medium underline"
											href="https://ldap.com/ldap-filters/"
											target="_blank"
										>
											{$i18n.t('Click here for filter guides.')}
										</a>
									</div>
									<div>
										<div class="flex justify-between items-center text-sm">
											<div class="  font-medium">{$i18n.t('TLS')}</div>

											<div class="mt-1">
												<Switch bind:state={LDAP_SERVER.use_tls} />
											</div>
										</div>
										{#if LDAP_SERVER.use_tls}
											<div class="flex w-full gap-2">
												<div class="w-full">
													<div class=" self-center text-xs font-medium min-w-fit mb-1 mt-1">
														{$i18n.t('Certificate Path')}
													</div>
													<input
														class="w-full bg-transparent outline-hidden py-0.5"
														placeholder={$i18n.t('Enter certificate path')}
														bind:value={LDAP_SERVER.certificate_path}
													/>
												</div>
											</div>
											<div class="flex justify-between items-center text-xs">
												<div class=" font-medium">{$i18n.t('Validate certificate')}</div>

												<div class="mt-1">
													<Switch bind:state={LDAP_SERVER.validate_cert} />
												</div>
											</div>
											<div class="flex w-full gap-2">
												<div class="w-full">
													<div class=" self-center text-xs font-medium min-w-fit mb-1">
														{$i18n.t('Ciphers')}
													</div>
													<Tooltip content={$i18n.t('Default to ALL')} placement="top-start">
														<input
															class="w-full bg-transparent outline-hidden py-0.5"
															placeholder={$i18n.t('Example: ALL')}
															bind:value={LDAP_SERVER.ciphers}
														/>
													</Tooltip>
												</div>
												<div class="w-full"></div>
											</div>
										{/if}
									</div>
								</div>
							{/if}
						</div>
					</div>
				</div>

				<div class="mb-3">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Features')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Enable Community Sharing')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_COMMUNITY_SHARING} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">{$i18n.t('Enable Message Rating')}</div>

						<Switch bind:state={adminConfig.ENABLE_MESSAGE_RATING} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Folders')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_FOLDERS} />
					</div>

					{#if adminConfig.ENABLE_FOLDERS}
						<div class="mb-2.5 w-full justify-between">
							<div class="flex w-full justify-between">
								<div class=" self-center text-xs font-medium">
									{$i18n.t('Folder Max File Count')}
								</div>
							</div>

							<div class="flex mt-2 space-x-2">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									type="number"
									min="0"
									placeholder={$i18n.t('Leave empty for unlimited')}
									bind:value={adminConfig.FOLDER_MAX_FILE_COUNT}
								/>
							</div>

							<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('Maximum number of files allowed per folder.')}
							</div>
						</div>
					{/if}

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Memories')} ({$i18n.t('Beta')})
						</div>

						<Switch bind:state={adminConfig.ENABLE_MEMORIES} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Notes')} ({$i18n.t('Beta')})
						</div>

						<Switch bind:state={adminConfig.ENABLE_NOTES} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Channels')} ({$i18n.t('Beta')})
						</div>

						<Switch bind:state={adminConfig.ENABLE_CHANNELS} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Calendar')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_CALENDAR} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Automations')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_AUTOMATIONS} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('User Webhooks')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_USER_WEBHOOKS} />
					</div>

					<div class="mb-2.5 flex w-full items-center justify-between pr-2">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('User Status')}
						</div>

						<Switch bind:state={adminConfig.ENABLE_USER_STATUS} />
					</div>

					<div class="mb-2.5">
						<div class=" self-center text-xs font-medium mb-2">
							{$i18n.t('Response Watermark')}
						</div>
						<Textarea
							placeholder={$i18n.t('Enter a watermark for the response. Leave empty for none.')}
							bind:value={adminConfig.RESPONSE_WATERMARK}
						/>
					</div>

					<div class="mb-2.5 w-full justify-between">
						<div class="flex w-full justify-between">
							<div class=" self-center text-xs font-medium">{$i18n.t('WebUI URL')}</div>
						</div>

						<Tooltip content={$i18n.t('Set the WEBUI_URL environment variable to change this value.')} placement="top" className="w-full">
							<div class="flex mt-2 space-x-2 w-full">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden disabled:opacity-60 disabled:cursor-not-allowed"
									type="text"
									placeholder="https://example.com"
									bind:value={adminConfig.WEBUI_URL}
									disabled
								/>
							</div>
						</Tooltip>

						<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Controlled by the WEBUI_URL environment variable. Cannot be changed here.')}
						</div>
					</div>

					<div class=" w-full justify-between">
						<div class="flex w-full justify-between">
							<div class=" self-center text-xs font-medium">{$i18n.t('Webhook URL')}</div>
						</div>

						<div class="flex mt-2 space-x-2">
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								type="text"
								placeholder={`https://example.com/webhook`}
								bind:value={webhookUrl}
							/>
						</div>
					</div>
=======
					{:else}
						<a
							class="mt-0.5 block text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							href="https://docs.openwebui.com/enterprise"
							target="_blank"
						>
							{$i18n.t(
								'Upgrade to a licensed plan for enhanced capabilities, including custom theming and branding, and dedicated support.'
							)}
						</a>
					{/if}
>>>>>>> v0.11.0
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Features')}>
				<AdminSettingRow
					label={$i18n.t('Community Sharing')}
					description={$i18n.t('Allow users to share chats with the Open WebUI community.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_COMMUNITY_SHARING} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Message Rating')}
					description={$i18n.t('Let users rate assistant responses.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_MESSAGE_RATING} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Folders')}
					description={$i18n.t('Allow users to organize chats into folders.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_FOLDERS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig.ENABLE_FOLDERS}
					<AdminSettingField
						label={$i18n.t('Folder Max File Count')}
						description={$i18n.t('Maximum number of files allowed per folder.')}
					>
						<input
							class={inputClass}
							type="number"
							min="0"
							placeholder={$i18n.t('Leave empty for unlimited')}
							bind:value={adminConfig.FOLDER_MAX_FILE_COUNT}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('Memories')}
					description={$i18n.t('Allow users to save memories for more personalized responses.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_MEMORIES} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				{#if adminConfig.ENABLE_MEMORIES}
					<AdminSettingRow
						label={$i18n.t('Memory System Context')}
						description={$i18n.t('Include saved memories in the system context.')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<Switch
							bind:state={adminConfig.ENABLE_MEMORY_SYSTEM_CONTEXT}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>
				{/if}
				<AdminSettingRow
					label={$i18n.t('Notes')}
					description={$i18n.t('Allow users to create and manage notes.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_NOTES} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Channels')}
					description={$i18n.t('Allow users to use channels for shared conversations.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CHANNELS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				{#if adminConfig.ENABLE_CHANNELS}
					<AdminSettingRow
						label={$i18n.t('Model Response Mode')}
						description={$i18n.t(
							'Choose where model responses to root-level channel mentions are posted.'
						)}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<SettingsSelect
							bind:value={adminConfig.CHANNEL_MODEL_RESPONSE_MODE}
							aria-labelledby={labelId}
						>
							<option value="thread">{$i18n.t('Thread')}</option>
							<option value="channel">{$i18n.t('Channel')}</option>
						</SettingsSelect>
					</AdminSettingRow>
				{/if}
				<AdminSettingRow
					label={$i18n.t('Calendar')}
					description={$i18n.t('Allow users to access calendar features.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CALENDAR} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('Automations')}
					description={$i18n.t('Allow users to create and run automations.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_AUTOMATIONS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('User Webhooks')}
					description={$i18n.t('Allow users to configure webhooks from their account.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_WEBHOOKS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('User Status')}
					description={$i18n.t('Show user status information in the app.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_STATUS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingField
					label={$i18n.t('Response Watermark')}
					description={$i18n.t('Append a watermark to assistant responses when configured.')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t('Enter a watermark for the response. Leave empty for none.')}
						bind:value={adminConfig.RESPONSE_WATERMARK}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('WebUI URL')}
					description={$i18n.t(
						'Enter the public URL of your WebUI. This URL will be used to generate links in the notifications.'
					)}
				>
					<input
						class={inputClass}
						type="text"
						placeholder={`e.g.) "http://localhost:3000"`}
						bind:value={adminConfig.WEBUI_URL}
					/>
				</AdminSettingField>
			</AdminSettingSection>

			<Events />

			<AdminSettingSection title={$i18n.t('UI')}>
				<div>
					<div class="mb-2 flex w-full items-start justify-between gap-4">
						<div class="min-w-0">
							<div class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Banners')}</div>
							<div class="mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
								{$i18n.t('Create announcements shown to users in the app.')}
							</div>
<<<<<<< HEAD

							<button
								class="p-1 px-3 text-xs flex rounded-sm transition"
								type="button"
								aria-label={$i18n.t('Add Banner')}
								on:click={() => {
									if (banners.length === 0 || banners.at(-1).content !== '') {
										banners = [
											...banners,
											{
												id: uuidv4(),
												type: '',
												title: '',
												content: '',
												dismissible: true,
												timestamp: Math.floor(Date.now() / 1000)
											}
										];
									}
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="w-4 h-4"
								>
									<path
										d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z"
									/>
								</svg>
							</button>
=======
>>>>>>> v0.11.0
						</div>

						<button
							class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-900 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-white"
							type="button"
							aria-label={$i18n.t('Add banner')}
							on:click={() => {
								if (banners.length === 0 || banners[banners.length - 1]?.content !== '') {
									banners = [
										...banners,
										{
											id: uuidv4(),
											type: '',
											title: '',
											content: '',
											dismissible: true,
											timestamp: Math.floor(Date.now() / 1000)
										}
									];
								}
							}}
						>
							<Plus />
						</button>
					</div>

					<Banners bind:banners />
				</div>
			</AdminSettingSection>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
