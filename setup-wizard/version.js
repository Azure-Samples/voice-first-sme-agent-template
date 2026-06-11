const SEMVER_RE = /^\d+\.\d+\.\d+$/;

function classifyVersion(value) {
    if (value === undefined || value === null || value === '') {
        return { kind: 'missing' };
    }
    if (Number.isInteger(value)) {
        return { kind: 'legacy' };
    }
    if (typeof value === 'string' && SEMVER_RE.test(value)) {
        return {
            kind: 'semver',
            parts: value.split('.').map(n => parseInt(n, 10)),
        };
    }
    throw new Error(`Invalid template version "${value}". Expected MAJOR.MINOR.PATCH.`);
}

// Compare two MAJOR.MINOR.PATCH versions.
//
// Integer values are legacy settings schema versions, not template releases,
// so they sort older than any real semver release. Missing values mean "no
// prior template version recorded" and compare equal by design; callers that
// need to distinguish missing values should handle them before calling this.
export function compareSemver(a, b) {
    const va = classifyVersion(a);
    const vb = classifyVersion(b);

    if (va.kind === 'missing' || vb.kind === 'missing') return 0;
    if (va.kind === 'legacy' && vb.kind === 'legacy') return 0;
    if (va.kind === 'legacy' && vb.kind === 'semver') return -1;
    if (va.kind === 'semver' && vb.kind === 'legacy') return 1;

    for (let i = 0; i < 3; i++) {
        if (va.parts[i] < vb.parts[i]) return -1;
        if (va.parts[i] > vb.parts[i]) return 1;
    }
    return 0;
}

export function versionMigrationWarnings(settingsVersion, templateVersion) {
    if (settingsVersion === undefined || settingsVersion === null || settingsVersion === '') {
        return [];
    }

    const versionCompare = compareSemver(settingsVersion, templateVersion);
    if (versionCompare < 0) {
        return [
            `Your settings.json was created with template version ${settingsVersion}. The current template is ${templateVersion}. Running setup will migrate your settings.`,
        ];
    }
    if (versionCompare > 0) {
        return [
            `Your settings.json was created with newer template version ${settingsVersion}. The current checkout is ${templateVersion}.`,
            'Continuing may rewrite settings.json for this older template checkout.',
        ];
    }
    return [];
}
