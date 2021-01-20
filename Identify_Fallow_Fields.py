###############################################################################################
###############################################################################################

# Name:             Identify_Fallow_Fields.py
# Author:           Kelly Meehan, USBR
# Created:          20200501
# Updated:          20210120 
# Version:          Created using Python 3.6.8 

# Requires:         ArcGIS Pro 

# Notes:            This script is intended to be used for a Script Tool within ArcGIS Pro; it is not intended as a stand-alone script.

# Description:      This tool generates shapefile and raster subsets of a field border shapefile and satellite image(s), respectively.  

################################################################################################
################################################################################################

# This script will:
# 0. Set up
# 1. Subset area of interest
# 2. Calculate NDVI

#----------------------------------------------------------------------------------------------

# 0. Set up 

# 0.0 Import necessary packages
import arcpy, os, glob, pandas, numpy, fnmatch
from datetime import datetime, timedelta

#--------------------------------------------

# 0.1 Read in tool parameters

# User sepecifies imagery directory
imagery_directory = arcpy.GetParameterAsText(1)

# User specifies output directory
output_directory = arcpy.GetParameterAsText(2)

# User specifies feature class with agricultural fields for analysis
ground_truth_feature_class = arcpy.GetParameterAsText(3)

# User specifies name of region as name without spaces
region = arcpy.GetParameterAsText(4)

# User specifies file geodatabase
geodatabase = arcpy.GetParameterAsText(5)

#--------------------------------------------

# 0.2 Set environment settings

# Set overwrite permissions to true in case user reruns tool (and redraws aoi)
arcpy.env.overwriteOuptut = True

# Change working directory to output directory
os.chdir(imagery_directory)

# Set workspace to output directory
arcpy.env.workspace = imagery_directory

#--------------------------------------------

# 0.3 Check out spatial analyst extension
arcpy.CheckOutExtension('Spatial')

#--------------------------------------------------------------------------

# 1. Calculate NDVI

# Create list of all files in Imagery Directory
file_list = os.listdir()

# Assign variable to empty list (to which .img files to be iterated through will be added)
imgery_list = []

# Create list of composite rasters
imgery_list = glob.glob('*_B2-4_8.img')

def calculate_ndvi():

    # Create empty lists to which dates will later be added
    date_list = []
    
    for i in imgery_list:
        image_name = os.path.basename(i) 
        image_name_chunks = image_name.split('_')
        image_date = image_name_chunks[2]
        
        # Clip image by subset area of interest      
        outExtractByMask = arcpy.sa.ExtractByMask(in_raster = i, in_mask_data = ground_truth_feature_class)
        
        # Save extracted image to memory
        output = r'in_memory\subset_raster_' + image_date
        
        # Check for pre-existing raster and delete
        if arcpy.Exists(output):
            arcpy.Delete_management(in_data = output)
        
        outExtractByMask.save(output)
       
        # Read in NIR and Red bands
        nir_raster = arcpy.Raster(output + '\\Band_4')
        red_raster = arcpy.Raster(output + '\\Band_3')
        
        # Generate two new rasters, the first as the top of the ndvi calculation, the second the bottom
        # NOTE: arcpy.sa.Raster function required to read in layer as a raster object and Float function is used to avoid integer outputs
        numerator = arcpy.sa.Float(nir_raster - red_raster)
        denominator = arcpy.sa.Float(nir_raster + red_raster)
        
        # Generate a third raster (in memory) of ndvi (numerator divided by denominator)
        ndvi_output = arcpy.sa.Divide(numerator, denominator)
        
        # Extract date from file name and append to list
        date_list.append(image_date)
        
        # Assign variable to zonal statistics table
        majority_table = r'in_memory/' + region + '_ndvi_' + image_date
    
        # Check for pre-existing table and delete
        if arcpy.Exists(majority_table):
            arcpy.Delete_management(in_data = majority_table)
        
        # Generat zenal statistics (mean) table
        arcpy.sa.ZonalStatisticsAsTable(in_zone_data = ground_truth_feature_class, zone_field = 'FIELD_ID', in_value_raster = ndvi_output, out_table = majority_table, statistics_type = 'MEAN')

        # Check for pre-existing attribute table field and delete        
        if 'ndvi_' + image_date in [field.name for field in arcpy.ListFields(ground_truth_feature_class)]:
            arcpy.DeleteField_management(in_table = ground_truth_feature_class, drop_field = 'ndvi_' + image_date)
            
        # Join table to area of interest subset
        arcpy.JoinField_management(in_data = ground_truth_feature_class, in_field = 'FIELD_ID', join_table = majority_table, join_field  = 'FIELD_ID', fields = 'MEAN')
        
        # Change name of joined attribute table field 
        arcpy.AlterField_management(in_table = ground_truth_feature_class, field = 'MEAN', new_field_name = 'ndvi_' + image_date, new_field_alias = 'ndvi_' + image_date)
        
        # Check for lingering MEAN attribute table field and delete
        if 'MEAN' in [field.name for field in arcpy.ListFields(ground_truth_feature_class)]:
            arcpy.DeleteField_management(in_table = ground_truth_feature_class, drop_field = 'MEAN')
    
calculate_ndvi()

#--------------------------------------------------------------------------

# 2. Calculate delta NDVI for time periods (save for first)

# Create list of attribute table fields to include in numpy array (ndvi* and FIELD_ID)

include_fields = [field.name for field in arcpy.ListFields(dataset = ground_truth_feature_class, wild_card = 'ndvi*')]
include_fields.insert(0, 'FIELD_ID')

# Create numpy array from Training Label Signame Geodatabase Table
array_ndvi = arcpy.da.TableToNumPyArray(in_table = ground_truth_feature_class, field_names = include_fields)

# Create pandas data frame from numpy array
df_ndvi = pandas.DataFrame(data = array_ndvi)

# Set FIELD_ID column as index
df_ndvi.set_index('FIELD_ID', inplace = True)

# Create data frame calculating delta NDVI 
df_delta_ndvi = df_ndvi.diff(axis = 1)

# Change column names to indicate delta NDVI
df_delta_ndvi.columns = [col.replace('ndvi', 'delta') for col in df_delta_ndvi.columns]

# Delete first delta NDVI column with no data
df_delta_ndvi.dropna(axis = 1, how = 'all', inplace = True)

#--------------------------------------------------------------------------

# 3. Identify most recent harvest date
   
def get_recent_harvest(v):
    s = pandas.Series(v < -0.15)
    array = s.where(s == True).last_valid_index()
    return numpy.nan if array is None else array[6:]

df_delta_ndvi['Harvest_Date'] = df_delta_ndvi.apply(lambda x: get_recent_harvest(x), axis = 1)

# Join delta NDVI dataframe
df_ndvi = df_ndvi.join(df_delta_ndvi, how = 'outer')

#--------------------------------------------------------------------------

# 4. Identify fallow fields

# Assign variable to integer value of today's date as YYYYMMDD
date_today = int(datetime.today().strftime('%Y%m%d'))

# Create list of all dataframe column names
columns_all = list(df_ndvi.columns.values)

# Create list of delta NDVI column names
columns_ndvi = fnmatch.filter(columns_all, 'ndvi*')

# Create list of delta NDVI column names
columns_delta = fnmatch.filter(columns_all, 'delta_*')

# Create list of NDVI dates as integers
dates = [d.replace('ndvi_', '') for d in columns_ndvi]
dates_as_integers = [int(d) for d in dates]

# Assign variable to integer value of 28 days prior to runtime as YYYYYMMFF
month_ago = datetime.now() - timedelta(30)
date_month_ago = int(month_ago.strftime("%Y%m%d"))

# Extract  dates within 28 days of runtime
dates_recent = [r for r in dates_as_integers if r >= date_month_ago]

# Identify columns with NDVI values within 28 days of report run
columns_ndvi_recent = ['ndvi_' + str(n) for n in dates_recent]

# Label those fields with NDVI < 0.2 for the past 30 days as Fallow
df_ndvi['Fallow_Status'] = numpy.where((df_ndvi[columns_ndvi_recent] < 0.2).all(axis = 1), 'Fallow', 'Not Fallow')
numpy.where(())

len(df_ndvi[df_ndvi.Fallow_Status == 'Fallow'])

# Override fallow label for exceptions including fields whose most recent NDVI > 0.14 or one of the two most recent delta NDVIs are > 0.02

ultima_ndvi = columns_ndvi[-1]
ultima_delta = columns_delta[-1]
penultima_delta = columns_delta[-2]

for index, row in df_ndvi.iterrows():
    if  df_ndvi.loc[index, ultima_ndvi] > 0.14 and (df_ndvi.loc[index, ultima_delta] > 0.02 or df_ndvi.loc[index, penultima_delta] > 0.02):
        df_ndvi.loc[index, 'Fallow_Status'] = 'Not_Fallow'

len(df_ndvi[df_ndvi.Fallow_Status == 'Fallow'])
        
#--------------------------------------------------------------------------

# 5. Join pandas dataframe to Ground Truth feature class
columns_all = list(df_ndvi.columns.values)
columns_non_ndvi = [c for c in columns_all if c not in columns_ndvi]
df_join = df_ndvi[columns_non_ndvi]

df_join['FIELD_ID'] = df_join.index

output_array = numpy.array(numpy.rec.fromrecords(df_join))
array_names = df_join.dtypes.index.tolist()
output_array.dtype.names = tuple(array_names)

arcpy.da.ExtendTable(in_table = ground_truth_feature_class, table_match_field = 'FIELD_ID', in_array = output_array, array_match_field = 'FIELD_ID')

 
#_______________________________________________________________________________

# TTDL

# Inner buffer fields and then calculate mean NDVI
# Use MEDIAN in zonal status(must first convert floating point to integer)
# Add boolean check mark to indicate whether to run append section or not 
# Fix formatting and numbering
# Add in region string name parameter
# Convert if checks for existing files of fields and use try except instead
# Note assumption of Sentinel imagery using nomenclature from tool 0.2*
# Add parameters to make suggested remove flexible: 1) number of days prior to harvest date, and 2) 
# Allow user to change NDVI value threshold for fallow (defalut to 0.2)
# Easier way of evaluating which NDVI columns are recent?
# Easier to make new data frame of recent NDVI or to only consider certain ones?
# How to deal with NA values in Harvest date in order to do comparison
# Use numpy to raster and do ndvi calculations with numpy or pandas
# Figure work around for error of extending table if fields already exist. In the case of delta_ndvi*, if this is being re-run it will cause error.
# Find some way to test if was recently watered using NDWI before increase in delta NDVI and set as not fallow 
# Catch emergent fields with low NDVI and low delta NDVI with heterogeneity index