<script lang="ts">
	import { onMount, getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { WEBUI_NAME, mobile, showSidebar, user } from '$lib/stores';
	import {
		getCalendars,
		getCalendarEvents,
		type CalendarModel,
		type CalendarEventModel
	} from '$lib/apis/calendar';
	import CalendarView from '$lib/components/calendar/CalendarView.svelte';
	import CalendarSidebar from '$lib/components/calendar/CalendarSidebar.svelte';
	import CalendarEventModal from '$lib/components/calendar/CalendarEventModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	let calendars: CalendarModel[] = [];
	let events: CalendarEventModel[] = [];
	let visibleCalendarIds: Set<string> = new Set();

	let view: 'month' | 'week' | 'day' = 'month';
	let currentDate = new Date();

	let showEventModal = false;
	let editEvent: CalendarEventModel | null = null;
	let defaultStartAt: number | null = null;

	function getVisibleRange(): { start: string; end: string } {
		const d = new Date(currentDate);
		let start: Date;
		let end: Date;

		if (view === 'month') {
			start = new Date(d.getFullYear(), d.getMonth(), 1);
			start.setDate(start.getDate() - start.getDay());
			end = new Date(start);
			end.setDate(end.getDate() + 42);
		} else if (view === 'week') {
			start = new Date(d);
			start.setDate(start.getDate() - start.getDay());
			start.setHours(0, 0, 0, 0);
			end = new Date(start);
			end.setDate(end.getDate() + 7);
		} else {
			start = new Date(d.getFullYear(), d.getMonth(), d.getDate());
			end = new Date(start);
			end.setDate(end.getDate() + 1);
		}

		return {
			start: start.toISOString(),
			end: end.toISOString()
		};
	}

	async function loadCalendars() {
		try {
			calendars = (await getCalendars(localStorage.token)) ?? [];
			visibleCalendarIds = new Set(calendars.map((c) => c.id));
		} catch (err) {
			console.error('loadCalendars', err);
			calendars = [];
		}
	}

	async function loadEvents() {
		try {
			const { start, end } = getVisibleRange();
			events = await getCalendarEvents(localStorage.token, start, end);
		} catch (err) {
			toast.error(`${err}`);
		}
	}

	async function refresh() {
		await loadEvents();
	}

	function toggleCalendar(id: string) {
		const next = new Set(visibleCalendarIds);
		if (next.has(id)) {
			next.delete(id);
		} else {
			next.add(id);
		}
		visibleCalendarIds = next;
	}

	function handleCreateEvent(e: CustomEvent<{ start_at: number }>) {
		editEvent = null;
		defaultStartAt = e.detail.start_at;
		showEventModal = true;
	}

	function handleEventClick(e: CustomEvent<CalendarEventModel>) {
		const evt = e.detail;
		if (evt.meta?.automation_id) {
			if (evt.meta?.chat_id) {
				goto(`/c/${evt.meta.chat_id}`);
			} else {
				goto(`/automations/${evt.meta.automation_id}`);
			}
			return;
		}
		editEvent = evt;
		defaultStartAt = null;
		showEventModal = true;
	}

	async function handleNavigate() {
		await tick();
		refresh();
	}

	async function handleDateSelect(date: Date) {
		currentDate = date;
		await tick();
		refresh();
	}

	function handleNewEvent() {
		editEvent = null;
		defaultStartAt = null;
		showEventModal = true;
	}

	$: defaultCalendarId = calendars.find((c) => c.is_default)?.id || calendars[0]?.id || '';

	onMount(async () => {
		await loadCalendars();
		await refresh();
		loaded = true;
	});
</script>

<svelte:head>
	<title>{$i18n.t('Calendar')} • {$WEBUI_NAME}</title>
</svelte:head>

<CalendarEventModal
	bind:show={showEventModal}
	event={editEvent}
	{calendars}
	{defaultCalendarId}
	{defaultStartAt}
	on:save={() => refresh()}
	on:delete={() => refresh()}
/>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	{#if loaded}
		<div class="flex flex-1 min-h-0">
			<!-- Sidebar -->
			<div class="hidden md:flex flex-col w-56 shrink-0 px-3 pt-3 overflow-y-auto">
				<button
					class="px-2 py-1.5 mt-0.5 mb-2.5 rounded-xl bg-black text-white dark:bg-white dark:text-black transition text-sm flex items-center justify-center gap-1"
					on:click={handleNewEvent}
				>
					<Plus className="size-3" strokeWidth="2.5" />
					<span class="text-xs">{$i18n.t('New Event')}</span>
				</button>

				<CalendarSidebar
					{calendars}
					{visibleCalendarIds}
					{currentDate}
					onToggle={toggleCalendar}
					onDateSelect={handleDateSelect}
				/>
			</div>

			<!-- Calendar -->
			<div class="flex-1 flex flex-col min-h-0 pt-0.5">
				<CalendarView
					{events}
					{calendars}
					{visibleCalendarIds}
					bind:view
					bind:currentDate
					on:createEvent={handleCreateEvent}
					on:eventClick={handleEventClick}
					on:navigate={handleNavigate}
					on:viewChange={handleNavigate}
					on:newEvent={handleNewEvent}
				/>
			</div>
		</div>
	{:else}
		<div class="w-full h-full flex justify-center items-center">
			<Spinner className="size-5" />
		</div>
	{/if}
</div>
