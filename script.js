const threatForm = document.querySelector('#threatForm');
const brandForm = document.querySelector('#brandForm');
const resultBox = document.querySelector('#resultBox');
const threatTable = document.querySelector('#threatTable');
const emptyRow = document.querySelector('#emptyRow');
let currentThreats = [];
let currentFilter = 'all';
let nameCheckTimer;

async function requestJson(path, options) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || 'The request could not be completed.');
    }
    return data;
}

function addCell(row, text, className) {
    const cell = document.createElement('td');
    cell.textContent = text;
    if (className) cell.className = className;
    row.appendChild(cell);
    return cell;
}

function makeStatusBadge(status) {
    const badge = document.createElement('span');
    badge.className = status.toLowerCase().replace(' risk', '');
    badge.textContent = status;
    return badge;
}

function renderThreats() {
    const filtered = currentThreats.filter((threat) =>
        currentFilter === 'all' || threat.candidate_type === currentFilter
    );
    threatTable.replaceChildren();
    emptyRow.hidden = filtered.length > 0;
    if (filtered.length === 0) {
        threatTable.appendChild(emptyRow);
        document.querySelector('#recordCount').textContent = '0 records';
        return;
    }

    for (const threat of filtered) {
        const row = document.createElement('tr');
        const identity = addCell(row, '');
        const name = document.createElement('strong');
        name.className = 'candidate-name';
        name.textContent = threat.name;
        const url = document.createElement('span');
        url.className = 'candidate-url';
        url.textContent = threat.url;
        identity.append(name, url);

        const channel = addCell(row, '');
        const type = document.createElement('span');
        type.className = 'type-label';
        type.textContent = threat.candidate_type === 'app'
            ? 'App store'
            : threat.candidate_type === 'website' ? 'Website' : 'Social';
        const platform = document.createElement('span');
        platform.className = 'candidate-url';
        platform.textContent = threat.platform + (threat.developer ? ` · ${threat.developer}` : '');
        channel.append(type, platform);

        const risk = addCell(row, '');
        const score = document.createElement('strong');
        score.className = 'risk-score';
        score.textContent = `${threat.risk_score}%`;
        risk.append(score, document.createElement('br'), makeStatusBadge(threat.status));

        const reasons = addCell(row, '');
        const reasonList = document.createElement('ul');
        reasonList.className = 'reason-list';
        for (const reason of threat.reasons || []) {
            const item = document.createElement('li');
            item.textContent = reason;
            reasonList.appendChild(item);
        }
        reasons.appendChild(reasonList);
        threatTable.appendChild(row);
    }
    document.querySelector('#recordCount').textContent =
        `${filtered.length} ${filtered.length === 1 ? 'record' : 'records'}`;
}

function renderOfficialList(profile) {
    const list = document.querySelector('#officialList');
    list.replaceChildren();
    const entries = [];
    if (profile.official_website) {
        entries.push({ type: 'Website', name: profile.brand_name, url: profile.official_website });
    }
    for (const account of profile.social_accounts || []) {
        entries.push({ type: account.platform || 'Social', name: 'Official account', url: account.url });
    }
    for (const application of profile.applications || []) {
        entries.push({
            type: 'Mobile app',
            name: `${application.name} · ${application.developer || 'Publisher not set'}`,
            url: application.url
        });
    }

    document.querySelector('#officialCount').textContent = entries.length;
    if (entries.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'muted-copy';
        empty.textContent = 'No official identities registered yet.';
        list.appendChild(empty);
        return;
    }

    for (const entry of entries) {
        const item = document.createElement('div');
        item.className = 'official-item';
        const kind = document.createElement('span');
        kind.className = 'type-label';
        kind.textContent = entry.type;
        const detail = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = entry.name;
        const url = document.createElement('span');
        url.className = 'candidate-url';
        url.textContent = entry.url;
        detail.append(name, url);
        item.append(kind, detail);
        list.appendChild(item);
    }
}

function fillBrandForm(profile) {
    document.querySelector('#brandName').value = profile.brand_name || '';
    document.querySelector('#officialWebsite').value = profile.official_website || '';
    document.querySelector('#socialAccounts').value = (profile.social_accounts || [])
        .map((account) => `${account.platform || 'Social'} | ${account.url}`)
        .join('\n');
    document.querySelector('#officialApps').value = (profile.applications || [])
        .map((application) => `${application.name} | ${application.developer || ''} | ${application.url}`)
        .join('\n');
    document.querySelector('#brandIdentifiers').value = (profile.identifiers || []).join('\n');

    const preview = document.querySelector('#brandLogoPreview');
    preview.hidden = !profile.logo_path;
    if (profile.logo_path) preview.src = profile.logo_path;
    document.querySelector('#candidateLogoField').hidden = !profile.logo_path;
    document.querySelector('#welcomeTitle').textContent = profile.brand_name
        ? `Protect ${profile.brand_name}`
        : 'Protect your brand identity';
}

async function refreshDashboard() {
    const [threats, stats, profile] = await Promise.all([
        requestJson('/api/threats'),
        requestJson('/api/dashboard/stats'),
        requestJson('/api/brand')
    ]);
    currentThreats = threats;
    document.querySelector('#totalThreats').textContent = stats.totalThreats;
    document.querySelector('#highRisk').textContent = stats.highRisk;
    fillBrandForm(profile);
    renderOfficialList(profile);
    renderThreats();
}

function selectPanel(panelId) {
    for (const button of document.querySelectorAll('.tab-button')) {
        const selected = button.dataset.panel === panelId;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-selected', String(selected));
        document.querySelector(`#${button.dataset.panel}`).hidden = !selected;
    }
}

function updateCandidateFields() {
    const type = document.querySelector('#candidateType').value;
    const platform = document.querySelector('#platform');
    const social = type === 'social';
    document.querySelector('#socialPlatformField').hidden = !social;
    document.querySelector('#developerField').hidden = type !== 'app';
    if (!social) {
        platform.value = '';
    } else if (!platform.value) {
        platform.selectedIndex = 0;
    }
}

function displayReport(report) {
    resultBox.replaceChildren();
    resultBox.hidden = false;
    resultBox.classList.toggle('excluded-result', report.excluded === true);
    const heading = document.createElement('strong');
    heading.textContent = report.excluded
        ? report.status
        : `${report.flagged ? 'Flagged' : 'No suspicious signals'} · ${report.status} · ${report.risk_score}%`;
    resultBox.appendChild(heading);
    const reasons = document.createElement('ul');
    for (const reason of report.reasons || []) {
        const item = document.createElement('li');
        item.textContent = reason;
        reasons.appendChild(item);
    }
    resultBox.appendChild(reasons);
}

for (const button of document.querySelectorAll('.tab-button')) {
    button.addEventListener('click', () => selectPanel(button.dataset.panel));
}

for (const button of document.querySelectorAll('.filter-button')) {
    button.addEventListener('click', () => {
        currentFilter = button.dataset.filter;
        for (const filter of document.querySelectorAll('.filter-button')) {
            const selected = filter === button;
            filter.classList.toggle('active', selected);
            filter.setAttribute('aria-pressed', String(selected));
        }
        renderThreats();
    });
}

document.querySelector('#refreshButton').addEventListener('click', async () => {
    const button = document.querySelector('#refreshButton');
    button.disabled = true;
    try {
        await refreshDashboard();
    } catch (error) {
        document.querySelector('#recordCount').textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

document.querySelector('#candidateType').addEventListener('change', updateCandidateFields);
updateCandidateFields();

brandForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = brandForm.querySelector('button[type="submit"]');
    const status = document.querySelector('#brandSaveStatus');
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
        const accounts = document.querySelector('#socialAccounts').value
            .split('\n').map((line) => line.trim()).filter(Boolean);
        const applications = document.querySelector('#officialApps').value
            .split('\n').map((line) => line.trim()).filter(Boolean);
        const identifiers = document.querySelector('#brandIdentifiers').value
            .split('\n').map((line) => line.trim()).filter(Boolean);
        await requestJson('/api/brand', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                brand_name: document.querySelector('#brandName').value,
                official_website: document.querySelector('#officialWebsite').value,
                social_accounts: accounts,
                applications,
                identifiers
            })
        });

        const logo = document.querySelector('#brandLogo').files[0];
        if (logo) {
            const data = new FormData();
            data.append('logo', logo);
            await requestJson('/api/brand/logo', { method: 'POST', body: data });
            document.querySelector('#brandLogo').value = '';
        }
        status.textContent = 'Profile saved';
        await refreshDashboard();
    } catch (error) {
        status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

threatForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.querySelector('#analyzeButton');
    const status = document.querySelector('#monitorStatus');
    button.disabled = true;
    status.textContent = 'Checking…';

    try {
        const data = new FormData();
        data.append('candidate_type', document.querySelector('#candidateType').value);
        data.append('platform', document.querySelector('#platform').value);
        data.append('name', document.querySelector('#threatName').value);
        data.append('url', document.querySelector('#threatUrl').value);
        data.append('developer', document.querySelector('#candidateDeveloper').value);
        data.append('description', document.querySelector('#candidateDescription').value);
        const logo = document.querySelector('#candidateLogo').files[0];
        if (logo) data.append('candidate_logo', logo);

        const report = await requestJson('/api/monitor', { method: 'POST', body: data });
        displayReport(report);
        status.textContent = report.excluded ? 'Official identity excluded' : 'Review complete';
        if (!report.excluded) await refreshDashboard();
        if (!report.excluded) {
            threatForm.reset();
            updateCandidateFields();
            document.querySelector('#nameMatchHint').textContent = '';
        }
    } catch (error) {
        resultBox.hidden = false;
        resultBox.classList.remove('excluded-result');
        resultBox.textContent = error.message;
        status.textContent = 'Check failed';
    } finally {
        button.disabled = false;
    }
});

document.querySelector('#threatName').addEventListener('input', (event) => {
    clearTimeout(nameCheckTimer);
    const hint = document.querySelector('#nameMatchHint');
    const name = event.target.value.trim();
    hint.textContent = '';
    if (!name) return;
    nameCheckTimer = setTimeout(async () => {
        try {
            const result = await requestJson(`/api/name-check?name=${encodeURIComponent(name)}`);
            hint.textContent = result.matches.map((match) =>
                `${match.official_name}: ${match.match_type}`
            ).join(' · ');
            hint.classList.toggle('match-warning', result.matches.length > 0);
        } catch {
            hint.textContent = '';
        }
    }, 250);
});

refreshDashboard().catch((error) => {
    document.querySelector('#recordCount').textContent = `Could not load data: ${error.message}`;
});