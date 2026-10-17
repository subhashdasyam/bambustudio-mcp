// This helper is built externally against an unchanged, matching Studio source tree.
#include "libslic3r/PrintConfig.hpp"
#include "nlohmann/json.hpp"
#include <iostream>

int main()
{
    using namespace Slic3r;
    using nlohmann::json;
    json options = json::object();
    const auto object_keys = PrintObjectConfig().keys();
    const auto region_keys = PrintRegionConfig().keys();
    for (const auto& [key, def] : print_config_def.options) {
        if (def.printer_technology == ptSLA) continue;
        json entry = {{"type_id", int(def.type)}, {"nullable", def.nullable},
                      {"label", def.label}, {"description", def.tooltip}, {"units", def.sidetext},
                      {"readonly", def.readonly}, {"category", def.category},
                      {"ratio_over", def.ratio_over}, {"aliases", def.aliases}, {"enum_values", def.enum_values}};
        entry["min"] = def.min == ConfigOptionDef::min_default ? json(nullptr) : json(def.min);
        entry["max"] = def.max == ConfigOptionDef::max_default ? json(nullptr) : json(def.max);
        entry["default_serialized"] = def.default_value ? json(def.default_value->serialize()) : json(nullptr);
        entry["object_scope"] = std::find(object_keys.begin(), object_keys.end(), key) != object_keys.end();
        entry["region_scope"] = std::find(region_keys.begin(), region_keys.end(), key) != region_keys.end();
        if (def.enum_keys_map != nullptr) {
            entry["enum_map"] = *def.enum_keys_map;
        }
        options[key] = std::move(entry);
    }
    std::cout << json{{"options", options}, {"metadata_source", "compiled_studio_definitions"}}.dump(2) << '\n';
}
