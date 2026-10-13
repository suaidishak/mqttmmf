export const PAGE_SIZE = 20;
export function formatNumber(value) {
  if (!Number.isFinite(value)) return '—';
  if (value !== 0 && Math.abs(value) < .001) return value.toExponential(3);
  return new Intl.NumberFormat('en', { maximumFractionDigits: 4 }).format(value);
}
export function unitText(p) { return p.unitConflict ? p.sourceUnits.join(' / ') + ' · conflict' : p.unit || 'Not supplied'; }
export function series(captures, parameter, hours) {
  if (!captures.length) return [];
  const end = Date.parse(captures.at(-1).capturedAt), start = end - hours * 3600000;
  return captures.filter(c => Date.parse(c.capturedAt) >= start).flatMap(c => {
    const p = c.parameters.find(p => p.parameterCode === parameter);
    return p ? [{ ...p, capturedAt: c.capturedAt, stationCount: c.stationCount }] : [];
  });
}
export function segments(points, gapMs = 1800000) {
  return points.reduce((groups, p, i) => {
    if (!i || Date.parse(p.capturedAt) - Date.parse(points[i - 1].capturedAt) > gapMs) groups.push([]);
    groups.at(-1).push(p); return groups;
  }, []);
}
export function csv(rows) {
  return rows.map(row => row.map(value => {
    let text = String(value ?? '');
    if (/^[\s]*[=+@-]/.test(text) && typeof value !== 'number') text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }).join(',')).join('\r\n') + '\r\n';
}
export function validateData(data) {
  if (!data || data.schemaVersion !== 1 || !Number.isFinite(Date.parse(data.generatedAt)) || !Array.isArray(data.sites) || data.sites.length > 64) throw new Error('Invalid data file');
  for (const site of data.sites) {
    if (typeof site.siteCode !== 'string' || !Array.isArray(site.captures) || site.captures.length > 192) throw new Error('Invalid site');
    let previous = -Infinity;
    for (const c of site.captures) {
      const time = Date.parse(c.capturedAt);
      if (!Number.isFinite(time) || time <= previous || !Number.isInteger(c.stationCount) || c.stationCount < 1 || c.stationCount > 64 || !Array.isArray(c.parameters) || c.parameters.length > 1024) throw new Error('Invalid capture');
      previous = time;
      const codes = new Set();
      for (const p of c.parameters) {
        if (typeof p.parameterCode !== 'string' || !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(p.parameterCode) || codes.has(p.parameterCode) || !Number.isFinite(p.averageValue) || !Number.isInteger(p.contributingStations) || p.contributingStations < 1 || p.contributingStations > c.stationCount || !Number.isInteger(p.stationsWithUnit) || p.stationsWithUnit < 0 || p.stationsWithUnit > p.contributingStations || typeof p.unitConflict !== 'boolean' || !Array.isArray(p.sourceUnits) || p.sourceUnits.length > 64 || p.sourceUnits.some(u=>typeof u!=='string'||u.length>64) || !(p.unit === null || typeof p.unit === 'string' && p.unit.length <= 64)) throw new Error('Invalid parameter');
        codes.add(p.parameterCode);
      }
    }
  }
  return data;
}
