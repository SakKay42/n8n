const machinesEl = document.querySelector('#machines');
const transactionsEl = document.querySelector('#transactions');
const eventsEl = document.querySelector('#events');

document.querySelector('#refresh').addEventListener('click', refresh);

async function api(path, options = {}) {
	const response = await fetch(path, {
		headers: { 'Content-Type': 'application/json' },
		...options,
	});
	if (!response.ok) {
		const error = await response.json();
		throw new Error(error.detail || response.statusText);
	}
	return response.json();
}

async function startMachine(machineId) {
	const program = Number(prompt('Program number', '1')) || 1;
	await api(`/machines/${machineId}/simulate-payment`, {
		method: 'POST',
		body: JSON.stringify({ program_number: program, payment_reference: `ui-${Date.now()}-${machineId}` }),
	});
	await refresh();
}

async function stopMachine(machineId) {
	await api(`/machines/${machineId}/stop`, { method: 'POST', body: '{}' });
	await refresh();
}

async function resetMachine(machineId) {
	await api(`/machines/${machineId}/reset`, { method: 'POST', body: '{}' });
	await refresh();
}

function renderMachines(machines) {
	machinesEl.innerHTML = machines.map((machine) => `
		<article class="card">
			<h3>${machine.name}<span class="badge ${machine.status}">${machine.status}</span></h3>
			<div class="meta">
				<span>Slave address: ${machine.slave_address}</span>
				<span>Type: ${machine.type}, ${machine.capacity_kg} kg</span>
				<span>Program: ${machine.program_number || '-'}</span>
				<span>Remaining: ${machine.remaining_minutes || 0} min</span>
				<span>Door: ${machine.door_state}</span>
				<span>Error: ${machine.error_code || 0} ${machine.error_message || ''}</span>
				<span>Last seen: ${machine.last_seen || 'never'}</span>
			</div>
			<div class="actions">
				<button onclick="startMachine('${machine.id}')">Simulate payment</button>
				<button class="secondary" onclick="stopMachine('${machine.id}')">Stop</button>
				<button class="secondary" onclick="resetMachine('${machine.id}')">Reset error</button>
			</div>
		</article>
	`).join('');
}

function renderTable(target, rows, columns) {
	if (!rows.length) {
		target.innerHTML = '<tr><td>No data yet</td></tr>';
		return;
	}
	target.innerHTML = `
		<thead><tr>${columns.map((column) => `<th>${column}</th>`).join('')}</tr></thead>
		<tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${row[column] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody>
	`;
}

async function refresh() {
	try {
		const [machines, transactions, events] = await Promise.all([
			api('/machines'),
			api('/transactions'),
			api('/events'),
		]);
		renderMachines(machines);
		renderTable(transactionsEl, transactions.slice(0, 10), ['created_at', 'machine_id', 'program_number', 'amount_cents', 'status']);
		renderTable(eventsEl, events.slice(0, 15), ['created_at', 'machine_id', 'level', 'event_type', 'message']);
	} catch (error) {
		alert(error.message);
	}
}

refresh();
setInterval(refresh, 3000);
