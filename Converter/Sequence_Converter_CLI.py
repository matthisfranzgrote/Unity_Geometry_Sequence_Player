#!/usr/bin/env python3
import os, re
from threading import Lock
import argparse
from Sequence_Converter import SequenceConverter, SequenceConverterSettings
from Sequence_Metadata import MetaData

VALID_MODEL_TYPES = ["obj", "3ds", "fbx", "glb", "gltf", "obj", "ply", "ptx", "stl", "xyz", "pts"]
VALID_IMAGE_TYPES = ["jpg", "jpeg", "png", "bmp", "tga"]
INVALID_IMAGE_TYPES = ["dds", "atsc"]

class ConverterCLI:
    verbose_logging: bool = False

    converter = SequenceConverter()

    models_paths_list: list[str] = []
    images_paths_list: list[str] = []

    preprocess_finished = True
    processed_file_count: int = 0
    progress_lock = Lock()

    await_conversion_start = False
    conversion_finished = False

    def tryint(self, s):
        try:
            return int(s)
        except ValueError:
            return s
    
    def alphanum_key(self, s):
        return [ self.tryint(c) for c in re.split('([0-9]+)', s) ]

    def human_sort(self, l):
        l.sort(key=self.alphanum_key)

    def validate_input_files(self, in_path) -> bool:
        if not os.path.exists(in_path):
            print(f"Input is invalid")
            return False

        files = os.listdir(in_path)

        self.models_paths_list = []
        self.images_paths_list = []

        # sort into model and image files
        for file in files:
            file_extension = file.split(".")[-1]

            if file_extension in VALID_MODEL_TYPES:
                self.models_paths_list.append(file)
            elif file_extension in VALID_IMAGE_TYPES:
                self.images_paths_list.append(file)
            elif file_extension in INVALID_IMAGE_TYPES:
                print("Can't convert already compressed (.dds, .astc) images! Please supply the images as .jpg, .png, .bmp or .tga!")
                return False

        if len(self.models_paths_list) < 1 and len(self.images_paths_list) < 1:
            print("No model/image files found in source directory!")
            return False

        if len(self.images_paths_list) > 1:
            textures_are_in_gamma_space = self.converter.get_image_gamme_encoded(os.path.join(in_path, self.images_paths_list[0]))
            if textures_are_in_gamma_space:
                print("Detected Textures that may be in Gamma colorspace. Consider running the conversion again using the SRGB converison flag.")

        self.human_sort(self.models_paths_list)
        self.human_sort(self.images_paths_list)

        # check if models are in compressed binary format
        modelPath = os.path.join(in_path, self.models_paths_list[0])
        with open(modelPath, 'rb') as f:
            text = f.read(200).decode('ascii', errors='ignore')
            if "half" in text:
                print("Sequence is already compressed! Please use the original sequence for conversion.")
                return False

        return True

    def validate_output_path(self, out_path) -> bool:
        if not os.path.exists(out_path):
            os.makedirs(out_path)
            return True
        # check that there is no converted sequence here already
        elif os.path.exists(os.path.join(out_path, "sequence.json")):
            print("There already seems to be a converted sequence at the destinations. Please use a different destination path.")
            return False
        else:
            return True

    def on_single_file_converion_finished(self, error, errorText):
        # use lock for thread-safe access to processed counter
        self.progress_lock.acquire()

        self.processed_file_count += 1

        if error:
            print(f"Error occurred during conversion: {errorText}")
            self.converter.terminate_conversion()
        elif self.verbose_logging:
            if not self.preprocess_finished:
                print(f"Compression preprocess: {self.processed_file_count}/{self.preprocess_file_count}")
            else:
                print(f"Conversion: {self.processed_file_count - self.preprocess_file_count}/{self.total_file_count - self.preprocess_file_count}")

        if self.preprocess_required and self.processed_file_count == self.preprocess_file_count:
            self.preprocess_finished = True
            print("Compression preprocess finished")
            # start main conversion now
            self.await_conversion_start = True

        if self.processed_file_count == self.total_file_count:
            self.conversion_finished = True
            print("Conversion finished")

        self.progress_lock.release()

    def start_main_conversion(self):
        print(f"Starting conversion using {convertSettings.maxThreads} threads")

        if not self.converter.start_conversion():
            print("An error occurred while starting the conversion process.")

    def run(self, convertSettings: SequenceConverterSettings, src: os.PathLike , dst: os.PathLike, verbose: bool = False):
        self.converter = SequenceConverter()
        self.verbose_logging = verbose

        if not self.validate_input_files(src):
            return
        if not self.validate_output_path(dst):
            return

        self.geoFileCount = len(self.models_paths_list)

        convert_images = convertSettings.convertToASTC or convertSettings.convertToDDS
        self.image_file_count = len(self.images_paths_list) if convert_images else 0

        self.preprocess_file_count = self.geoFileCount if convertSettings.useCompression else 0
        self.preprocess_required = convertSettings.useCompression
        self.preprocess_finished = not convertSettings.useCompression
            
        self.processed_file_count = 0
        self.total_file_count = self.geoFileCount + self.image_file_count + self.preprocess_file_count

        # fill in remaining convert settings
        convertSettings.modelPaths = self.models_paths_list
        convertSettings.imagePaths = self.images_paths_list
        
        self.converter.set_conversion_settings(convertSettings, self.on_single_file_converion_finished)

        if self.preprocess_required:
            print(f"Starting compression preprocess using {convertSettings.maxThreads} threads")
            self.converter.start_preprocessing()
        else:
            self.start_main_conversion()

        # need to do busy waiting, since we don't get the handle to the asnyc tasks to await
        # TODO: fix this by returning result from converter
        # TODO: might also be able improve threading performance by setting chunksize for map_async
        while not self.conversion_finished:
            if self.preprocess_required and self.await_conversion_start:
                self.start_main_conversion()
        else:
            self.converter.finish_conversion(True)

def decimation_pct(s: str) -> int:
    x = int(s)
    if not (0 <= x <= 100):
        raise argparse.ArgumentTypeError("decimation percentage must be in range 0..100")
    return x

def merge_distance(s: str) -> float:
    x = float(s)
    if not (x >= 0):
        raise argparse.ArgumentTypeError("merge distance must be non-negative")
    return x

def thread_count(s: str) -> int:
    x = int(s)
    if not (1 <= x <= 64):
        raise argparse.ArgumentTypeError("thread count must be in range [1..64]")
    return x

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="Geometry Sequence Converter CLI"
    )
    parser.add_argument(
        "-c", "--compression", 
        action="store_true",
        help="compress the geometry files"
    )
    parser.add_argument(
        "-sn", "--save-normals", 
        action="store_true",
        help="save normals if any exist on the input geometry (automatically enabled when generate normals flag is enabled)" 
    )
    # pointcloud settings
    parser.add_argument(
        "-d", "--decimate", 
        type=decimation_pct, 
        default=None,
        help="decimate pointcloud, pass percentage of points kept (pointclouds only)" 
    )
    parser.add_argument(
        "-m", "--merge", 
        type=merge_distance, 
        default=None,
        help="merge points by passed distance (pointclouds only)"
    )
    parser.add_argument(
        "-gn", "--generate-normals", 
        action="store_true",
        help="generate (estimate) normals if none are given (pointclouds only)"
    )
    parser.add_argument(
        "-in", "--invert-normals", 
        action="store_true",
        help="invert generated normals, no effect if normals are not generated (pointclouds only)"
    )
    # texture settings
    parser.add_argument(
        "-dds", "--convert-dds", 
        action="store_true",
        help="convert textures to compressed DDS format for desktop devices (applies to meshes only)"
    )
    parser.add_argument(
        "-astc", "--convert-astc", 
        action="store_true",
        help="convert textures to compressed ASTC format for mobile devices (applies to meshes only)"
    )
    parser.add_argument(
        "-srgb", "--convert-srgb", 
        action="store_true",
        help="convert textures to SRGB profile (applies to meshes only)"
    )
    # thread count
    parser.add_argument(
        "-t", "--threads", 
        type=thread_count, 
        default=8,
        help="number of threads used (default = 8)"
    )
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true",
        help="verbose logging"
    )
    # input/output (positional)
    parser.add_argument(
        "source",
        type=str,
        help="path to the source directory where the geometry and optional texture files are"
    )
    parser.add_argument(
        "destination",
        type=str,
        help="path to the destination directory where the geometry and optional texture files are"
    )

    args = parser.parse_args()

    applicationPath = os.path.abspath(os.path.dirname(__file__)) + os.sep

    resourcesPath = os.path.join(applicationPath, "resources") + os.sep

    convertSettings = SequenceConverterSettings()
    convertSettings.metaData = MetaData()
    convertSettings.inputPath = args.source
    convertSettings.outputPath = args.destination
    convertSettings.resourcePath = resourcesPath
    convertSettings.maxThreads = args.threads
    convertSettings.convertToDDS = args.convert_dds
    convertSettings.convertToASTC = args.convert_astc
    convertSettings.convertToSRGB = args.convert_srgb
    convertSettings.decimatePointcloud = args.decimate is not None
    convertSettings.decimatePercentage = args.decimate
    convertSettings.saveNormals = args.save_normals or args.generate_normals
    convertSettings.generateNormals = args.generate_normals
    convertSettings.invertNormals = args.generate_normals and args.invert_normals
    convertSettings.useCompression = args.compression
    convertSettings.mergePoints = args.merge is not None
    convertSettings.mergeDistance = args.merge

    CLI = ConverterCLI()
    CLI.run(convertSettings, args.source, args.destination, args.verbose)