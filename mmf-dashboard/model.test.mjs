import { test } from 'node:test';
import assert from 'node:assert/strict';
import { csv, series, segments, validateData, formatNumber } from './model.mjs';
const p = {parameterCode:'ghi',averageValue:0,contributingStations:2,unit:null,sourceUnits:[],unitConflict:false,stationsWithUnit:0};
const capture = (at,parameters=[p]) => ({capturedAt:at,stationCount:4,parameters});
test('zero is retained, missing parameters are not inserted as zero',()=>{
  const observations=series([capture('2026-01-01T00:00:00Z'),capture('2026-01-01T00:15:00Z',[])],'ghi',24);
  assert.equal(observations.length,1);assert.equal(observations[0].averageValue,0);assert.equal(observations[0].contributingStations,2);
});
test('history window uses latest actual capture and excludes older observations',()=>{
  const observations=series([capture('2026-01-01T00:00:00Z'),capture('2026-01-03T00:00:00Z')],'ghi',24);
  assert.equal(observations.length,1);assert.equal(observations[0].capturedAt,'2026-01-03T00:00:00Z');
});
test('long collection gaps break chart segments',()=>{
  assert.deepEqual(segments([capture('2026-01-01T00:00:00Z'),capture('2026-01-01T00:15:00Z'),capture('2026-01-01T02:00:00Z')]).map(s=>s.length),[2,1]);
});
test('CSV quoting and formula protection retain numeric negatives',()=>{
  assert.equal(csv([['=HYPERLINK("x")','normal, text',-2]]),'"\'=HYPERLINK(""x"")","normal, text","-2"\r\n');
});
test('malformed contributor counts and duplicate parameters fail validation',()=>{
  const data={schemaVersion:1,generatedAt:'2026-01-01T00:00:00Z',sites:[{siteCode:'TEST',captures:[capture('2026-01-01T00:00:00Z')]}]};
  assert.equal(validateData(data),data);
  const invalid=structuredClone(data);invalid.sites[0].captures[0].parameters[0].contributingStations=5;assert.throws(()=>validateData(invalid));
  const duplicate=structuredClone(data);duplicate.sites[0].captures[0].parameters.push(p);assert.throws(()=>validateData(duplicate));
});
test('small readings remain visible and empty values do not become zero',()=>{
  assert.equal(formatNumber(0),'0');assert.equal(formatNumber(null),'—');assert.match(formatNumber(.00001),/e-5$/);
});
