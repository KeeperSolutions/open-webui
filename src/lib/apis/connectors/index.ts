import { WEBUI_API_BASE_URL } from '$lib/constants';

export const connectConnector = (connectUrl: string): Promise<void> => {
	return new Promise((resolve) => {
		const width = 520;
		const height = 680;
		const left = window.screenX + (window.outerWidth - width) / 2;
		const top = window.screenY + (window.outerHeight - height) / 2;

		const popup = window.open(
			`${WEBUI_API_BASE_URL}${connectUrl}`,
			'connector-oauth',
			`width=${width},height=${height},left=${left},top=${top}`
		);

		if (!popup) {
			// Popup blocked - fall back to a full-page redirect
			window.location.href = `${WEBUI_API_BASE_URL}${connectUrl}`;
			resolve();
			return;
		}

		const interval = setInterval(() => {
			if (popup.closed) {
				clearInterval(interval);
				resolve();
			}
		}, 500);
	});
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

export const downloadGoogleDriveDocument = async (
	token: string,
	fileId: string,
	format: string,
	filename: string
) => {
	const params = new URLSearchParams({ format, filename });

	const blob = await fetch(
		`${WEBUI_API_BASE_URL}/connectors/google-drive/download/${fileId}?${params}`,
		{
			method: 'GET',
			headers: {
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.blob();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});

	return blob;
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
