import { queryOptions } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { settingEntries, settingSchema, type SettingsData } from './schema'

/** schema 与生效值一起读取，敏感项在进入缓存前就按 schema 脱敏。索引页与各分区共用这份缓存。 */
export const settingsQueryOptions = queryOptions({
  queryKey: keys.settings.all(),
  queryFn: async (): Promise<SettingsData> => {
    const [
      { data: schema, error: schemaError, response: schemaResponse },
      { data: settings, error: settingsError, response: settingsResponse },
    ] = await Promise.all([api.GET('/api/settings/schema'), api.GET('/api/settings')])
    if (schemaError) throw toApiError(schemaError, schemaResponse)
    if (settingsError) throw toApiError(settingsError, settingsResponse)
    const fields = settingSchema(schema)
    return { fields, values: settingEntries(settings, fields) }
  },
})
