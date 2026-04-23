# S6 Constraints and Vocabulary Mapping
#
# Format:
# - Substitution entries use: source => replacement
# - Forbidden entries use: forbidden: term

# Vocabulary substitution rules
sleep => couch potato
insomnia => deep evening rest
pain => relaxation
analgesic => physical comfort
anxiety => peace of mind

# Forbidden terms for S6 output mode
forbidden: s6
forbidden: dosage
forbidden: mg
forbidden: milligram
forbidden: prescription
forbidden: diagnose
