import { WEBUI_API_BASE_URL } from '$lib/constants';

export const connectConnector = (connectUrl: string) => {
	window.location.href = `${WEBUI_API_BASE_URL}${connectUrl}`;
};

export const getGoogleDriveStatus = async (token: string) => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/connectors/google-drive/status`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const disconnectGoogleDrive = async (token: string) => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/connectors/google-drive/disconnect`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
