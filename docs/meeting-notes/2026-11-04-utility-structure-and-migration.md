Meeting notes
Utility Structure 

- Somnath explained that the server contained separate sandbox and production utilities, including production migration scripts and Sailpointcrew update and upload utilities. The upload utility included scripts, logs, configuration parameters, temporary files, file state, and secrets storage
Feed File Management 

- Somnath stated that feed files were stored in the ISSB SFTV folder, with separate production and sandbox folders. Production incoming files used the `source name.csv` naming convention, while group files used `source name-group.csv`
Migration Utilities 

- Somnath described the migration utilities: Easy Transfer converted IIQ XML data into CSV in ISSB format, IIQ Export produced application XML exports, and IIQ Import created sources in IEC from the supplied data
- Somnath explained that the migration process used sequential stages to analyze IIQ XML sources, transfer the analyzed data, validate the transport data, load the validated output into ISSB, and generate a final report with the scorecard script
- Chandra clarified that CI/CD did not require the Migration application-creation and dependency files; it required the CSV files shown by Somnath
- Chandra noted that the Active Directory implementation differed and that the team might discuss it with Abhijeet in a later short call or the following week
Migration Pipeline 

- Somnath described a pipeline execution script that called the migration scripts in sequence and used an application CSV containing application name and application type as input; the selected applications were then created in the IEC environment
- Somnath explained that applications were split into different views during creation, with separate waves used for SQL Loader processing
- Participants discussed organizing the application files into connector-based folders, with aggregation data stored within the connector folders
Migration Waves 

- Somnath stated that delimited-file processing used a separate folder with two waves: the first tested one application, and the second created the remaining applications
2HK Process 

- Chandra said the upcoming discussion for 2HK would address the request to use a 14-day period, since other LBUs followed that period, and the expectation that the team follow a manual process despite having an automated system
Validation Evidence 

- Chandra clarified that validation screenshots could use simple checks, such as showing account provisioning completion in the activity section and account correlation in the identity cube; full-fledged SIT would be completed later
