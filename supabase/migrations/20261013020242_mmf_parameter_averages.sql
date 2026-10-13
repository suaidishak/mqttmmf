-- Separate owner-authorized MQTT derived-data store; does not alter Sites D1.
create table public.mmf_captures (
    site_code text not null check (length(site_code) between 1 and 64),
    captured_at timestamptz not null check (isfinite(captured_at)),
    station_count smallint not null check (station_count between 1 and 64),
    aggregation_method text not null default 'cross_station_arithmetic_mean'
        check (aggregation_method = 'cross_station_arithmetic_mean'),
    timestamp_basis text not null default 'collector_receipt_utc'
        check (timestamp_basis = 'collector_receipt_utc'),
    created_at timestamptz not null default now(),
    primary key (site_code, captured_at)
);

create table public.mmf_parameter_averages (
    site_code text not null,
    captured_at timestamptz not null,
    parameter_code text not null check (parameter_code ~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$'),
    average_value double precision not null
        check (average_value > '-Infinity'::double precision and average_value < 'Infinity'::double precision),
    contributing_stations smallint not null check (contributing_stations between 1 and 64),
    unit text check (unit is null or length(unit) <= 64),
    stations_with_unit smallint not null
        check (stations_with_unit between 0 and contributing_stations),
    primary key (site_code, captured_at, parameter_code),
    foreign key (site_code, captured_at) references public.mmf_captures (site_code, captured_at)
);

-- Equality on LSS and parameter, then range/order on capture time.
create index mmf_parameter_averages_series_idx
    on public.mmf_parameter_averages (site_code, parameter_code, captured_at desc)
    include (average_value, contributing_stations);

alter table public.mmf_captures enable row level security;
alter table public.mmf_parameter_averages enable row level security;
revoke all on public.mmf_captures, public.mmf_parameter_averages from public, anon, authenticated;
grant select, insert on public.mmf_captures, public.mmf_parameter_averages to service_role;

-- Invoker rights; service-role-only, validated and atomic. Identical retries
-- acknowledge the existing capture; conflicting payloads at a key are rejected.
create function public.store_mmf_capture(
    p_site_code text,
    p_captured_at timestamptz,
    p_station_count integer,
    p_parameters jsonb
) returns jsonb
language plpgsql security invoker set search_path = ''
as $$
declare
    inserted boolean := false;
    parameter_count integer;
    existing_count integer;
begin
    if current_user not in ('service_role', 'postgres') then
        raise exception 'Not authorized' using errcode = '42501';
    end if;
    if p_site_code is null or length(p_site_code) not between 1 and 64
       or p_captured_at is null or not isfinite(p_captured_at)
       or p_station_count is null or p_station_count not between 1 and 64
       or p_parameters is null or jsonb_typeof(p_parameters) <> 'array' then
        raise exception 'Invalid capture' using errcode = '22023';
    end if;
    parameter_count := jsonb_array_length(p_parameters);
    if parameter_count not between 1 and 1024 then
        raise exception 'Invalid parameter count' using errcode = '22023';
    end if;
    if exists (
        select 1 from jsonb_array_elements(p_parameters) e
        where jsonb_typeof(e) <> 'object'
           or jsonb_typeof(e->'parameterCode') is distinct from 'string'
           or jsonb_typeof(e->'averageValue') is distinct from 'number'
           or jsonb_typeof(e->'contributingStations') is distinct from 'number'
           or jsonb_typeof(e->'stationsWithUnit') is distinct from 'number'
           or coalesce(jsonb_typeof(e->'unit'), 'null') not in ('string', 'null')
    ) then
        raise exception 'Invalid parameter shape' using errcode = '22023';
    end if;
    if (select count(distinct e->>'parameterCode') from jsonb_array_elements(p_parameters) e) <> parameter_count then
        raise exception 'Duplicate parameter' using errcode = '22023';
    end if;
    if exists (
        select 1 from jsonb_to_recordset(p_parameters) as p(
            "parameterCode" text, "averageValue" double precision,
            "contributingStations" integer, unit text, "stationsWithUnit" integer)
        where p."parameterCode" !~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$'
           or p."averageValue" not between '-1.7976931348623157e308'::double precision and '1.7976931348623157e308'::double precision
           or p."contributingStations" not between 1 and p_station_count
           or p."stationsWithUnit" not between 0 and p."contributingStations"
           or length(p.unit) > 64
    ) then
        raise exception 'Invalid parameter values' using errcode = '22023';
    end if;

    insert into public.mmf_captures (site_code, captured_at, station_count)
    values (p_site_code, p_captured_at, p_station_count)
    on conflict (site_code, captured_at) do nothing
    returning true into inserted;

    if coalesce(inserted, false) then
        insert into public.mmf_parameter_averages
            (site_code, captured_at, parameter_code, average_value, contributing_stations, unit, stations_with_unit)
        select p_site_code, p_captured_at, p."parameterCode", p."averageValue", p."contributingStations", p.unit, p."stationsWithUnit"
        from jsonb_to_recordset(p_parameters) as p("parameterCode" text, "averageValue" double precision,
             "contributingStations" integer, unit text, "stationsWithUnit" integer);
    else
        select count(*) into existing_count from public.mmf_parameter_averages
        where site_code = p_site_code and captured_at = p_captured_at;
        if existing_count <> parameter_count
           or not exists (select 1 from public.mmf_captures
                          where site_code = p_site_code and captured_at = p_captured_at and station_count = p_station_count)
           or exists (
               select 1 from jsonb_to_recordset(p_parameters) as p("parameterCode" text, "averageValue" double precision,
                   "contributingStations" integer, unit text, "stationsWithUnit" integer)
               left join public.mmf_parameter_averages a
                 on a.site_code = p_site_code and a.captured_at = p_captured_at and a.parameter_code = p."parameterCode"
               where a.parameter_code is null or a.average_value is distinct from p."averageValue"
                  or a.contributing_stations is distinct from p."contributingStations"
                  or a.unit is distinct from p.unit or a.stations_with_unit is distinct from p."stationsWithUnit"
           ) then
            raise exception 'Capture key already contains different data' using errcode = '23505';
        end if;
    end if;
    return jsonb_build_object('parameterCount', parameter_count, 'inserted', coalesce(inserted, false));
end;
$$;
revoke all on function public.store_mmf_capture(text, timestamptz, integer, jsonb) from public, anon, authenticated;
grant execute on function public.store_mmf_capture(text, timestamptz, integer, jsonb) to service_role;

comment on table public.mmf_captures is 'Derived MQTT weather snapshots, uniquely identified by LSS code and actual UTC capture time.';
comment on table public.mmf_parameter_averages is 'One arithmetic mean per parameter across stations providing a fresh finite numeric reading; no raw source payloads.';
comment on column public.mmf_parameter_averages.parameter_code is 'Source parameter identifier; new identifiers add rows, not schema columns. No inferred aliases or unit conversion.';
comment on column public.mmf_parameter_averages.contributing_stations is 'The denominator for this parameter only; missing stations are not counted as zero.';
