-- Preserve all numeric means; source unit labels are metadata, not conversion rules.
alter table public.mmf_parameter_averages
    add column source_units text[] not null default '{}',
    add column unit_conflict boolean not null default false,
    add constraint mmf_source_units_bounded check (cardinality(source_units) <= 64),
    add constraint mmf_conflicting_unit_unqualified check (not unit_conflict or unit is null);
-- Backfill metadata only; previously captured averages are not changed.
update public.mmf_parameter_averages set source_units = array[unit] where unit is not null;
create or replace function public.store_mmf_capture(
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
           or coalesce(jsonb_typeof(e->'sourceUnits'), 'null') not in ('array', 'null')
           or coalesce(jsonb_typeof(e->'unitConflict'), 'null') not in ('boolean', 'null')
    ) then
        raise exception 'Invalid parameter shape' using errcode = '22023';
    end if;
    if (select count(distinct e->>'parameterCode') from jsonb_array_elements(p_parameters) e) <> parameter_count then
        raise exception 'Duplicate parameter' using errcode = '22023';
    end if;
    if exists (
        select 1 from jsonb_to_recordset(p_parameters) as p(
            "parameterCode" text, "averageValue" double precision,
            "contributingStations" integer, unit text, "stationsWithUnit" integer, "sourceUnits" text[], "unitConflict" boolean)
        where p."parameterCode" !~ '^[A-Za-z][A-Za-z0-9_.-]{0,127}$'
           or p."averageValue" not between '-1.7976931348623157e308'::double precision and '1.7976931348623157e308'::double precision
           or p."contributingStations" not between 1 and p_station_count
           or p."stationsWithUnit" not between 0 and p."contributingStations"
           or length(p.unit) > 64
           or cardinality(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)) > 64
           or array_position(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end), null) is not null
           or exists (select 1 from unnest(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)) label where length(label) > 64)
           or cardinality(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)) <> (select count(distinct label) from unnest(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)) label)
           or coalesce(p."unitConflict", false) <> (cardinality(coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)) > 1)
           or (p."sourceUnits" is not null and p.unit is distinct from
               case when cardinality(p."sourceUnits") = 1 then p."sourceUnits"[1] else null end)
    ) then
        raise exception 'Invalid parameter values' using errcode = '22023';
    end if;

    insert into public.mmf_captures (site_code, captured_at, station_count)
    values (p_site_code, p_captured_at, p_station_count)
    on conflict (site_code, captured_at) do nothing
    returning true into inserted;

    if coalesce(inserted, false) then
        insert into public.mmf_parameter_averages
            (site_code, captured_at, parameter_code, average_value, contributing_stations, unit, stations_with_unit, source_units, unit_conflict)
        select p_site_code, p_captured_at, p."parameterCode", p."averageValue", p."contributingStations", p.unit, p."stationsWithUnit", coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end), coalesce(p."unitConflict", false)
        from jsonb_to_recordset(p_parameters) as p("parameterCode" text, "averageValue" double precision,
             "contributingStations" integer, unit text, "stationsWithUnit" integer, "sourceUnits" text[], "unitConflict" boolean);
    else
        select count(*) into existing_count from public.mmf_parameter_averages
        where site_code = p_site_code and captured_at = p_captured_at;
        if existing_count <> parameter_count
           or not exists (select 1 from public.mmf_captures
                          where site_code = p_site_code and captured_at = p_captured_at and station_count = p_station_count)
           or exists (
               select 1 from jsonb_to_recordset(p_parameters) as p("parameterCode" text, "averageValue" double precision,
                   "contributingStations" integer, unit text, "stationsWithUnit" integer, "sourceUnits" text[], "unitConflict" boolean)
               left join public.mmf_parameter_averages a
                 on a.site_code = p_site_code and a.captured_at = p_captured_at and a.parameter_code = p."parameterCode"
               where a.parameter_code is null or a.average_value is distinct from p."averageValue"
                  or a.contributing_stations is distinct from p."contributingStations"
                  or a.unit is distinct from p.unit or a.stations_with_unit is distinct from p."stationsWithUnit"
                  or a.source_units is distinct from coalesce(p."sourceUnits", case when p.unit is null then '{}'::text[] else array[p.unit] end)
                  or a.unit_conflict is distinct from coalesce(p."unitConflict", false)
           ) then
            raise exception 'Capture key already contains different data' using errcode = '23505';
        end if;
    end if;
    return jsonb_build_object('parameterCount', parameter_count, 'inserted', coalesce(inserted, false));
end;
$$;
revoke all on function public.store_mmf_capture(text, timestamptz, integer, jsonb) from public, anon, authenticated;
grant execute on function public.store_mmf_capture(text, timestamptz, integer, jsonb) to service_role;


comment on column public.mmf_parameter_averages.source_units is 'Distinct raw source unit labels; no alias normalization or value conversion.';
comment on column public.mmf_parameter_averages.unit_conflict is 'True when contributors reported differing unit labels. Mean uses raw numbers at owner request; unit is null for conflicts.';
