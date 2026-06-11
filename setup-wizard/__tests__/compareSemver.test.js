import assert from 'node:assert/strict';
import test from 'node:test';

import { compareSemver, versionMigrationWarnings } from '../version.js';

test('compareSemver handles equal versions', () => {
    assert.equal(compareSemver('0.1.0', '0.1.0'), 0);
});

test('compareSemver detects older versions', () => {
    assert.equal(compareSemver('0.1.0', '0.2.0'), -1);
});

test('compareSemver detects newer versions', () => {
    assert.equal(compareSemver('0.2.0', '0.1.0'), 1);
});

test('compareSemver detects patch versions ahead', () => {
    assert.equal(compareSemver('0.1.1', '0.1.0'), 1);
});

test('compareSemver detects major versions ahead', () => {
    assert.equal(compareSemver('1.0.0', '0.99.99'), 1);
});

test('compareSemver treats integer settings version as legacy and older than semver', () => {
    assert.equal(compareSemver(1, '0.1.0'), -1);
});

test('compareSemver treats undefined as no prior version recorded', () => {
    assert.equal(compareSemver(undefined, '0.1.0'), 0);
});

test('compareSemver rejects malformed versions', () => {
    assert.throws(
        () => compareSemver('foo.bar.baz', '0.1.0'),
        /Invalid template version "foo\.bar\.baz"\. Expected MAJOR\.MINOR\.PATCH\./,
    );
});

test('versionMigrationWarnings warns once for legacy integer migration', () => {
    assert.deepEqual(
        versionMigrationWarnings(1, '0.1.0'),
        ['Your settings.json was created with template version 1. The current template is 0.1.0. Running setup will migrate your settings.'],
    );
});

test('versionMigrationWarnings skips missing version', () => {
    assert.deepEqual(versionMigrationWarnings(undefined, '0.1.0'), []);
});

test('versionMigrationWarnings warns when settings version is newer than checkout', () => {
    assert.deepEqual(
        versionMigrationWarnings('1.0.0', '0.1.0'),
        [
            'Your settings.json was created with newer template version 1.0.0. The current checkout is 0.1.0.',
            'Continuing may rewrite settings.json for this older template checkout.',
        ],
    );
});
